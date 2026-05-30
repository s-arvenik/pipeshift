# Technical Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                      AWS Transform Workspace                         │
│              (Chat UI, job orchestration, progress tracking)          │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     Amazon Bedrock AgentCore                          │
│  ┌─────────────────────┐    ┌──────────────────────────────────┐   │
│  │   Analyzer Agent     │    │       Converter Agent             │   │
│  │                      │    │                                   │   │
│  │  • XML Parser        │    │  • Expression Translator (LLM)   │   │
│  │  • Complexity Scorer │    │  • dbt Generator                 │   │
│  │  • Dependency Mapper │    │  • Glue Generator                │   │
│  │  • Report Generator  │    │  • Orchestration Generator       │   │
│  │                      │    │  • Validation Generator          │   │
│  └─────────────────────┘    └──────────────────────────────────┘   │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
     ┌──────────────┐   ┌─────────────┐   ┌──────────────────┐
     │ S3            │   │ Bedrock     │   │ Secrets Manager  │
     │ (artifacts,   │   │ (Claude for │   │ (source/target   │
     │  reports,     │   │  expression │   │  credentials)    │
     │  output code) │   │  translation│   │                  │
     └──────────────┘   └─────────────┘   └──────────────────┘
```

---

## Core Components

### 1. Source Parser (Deterministic)

Parses Informatica PowerCenter XML exports into a structured Intermediate Representation (IR).

**Input**: Informatica repository export XML files  
**Output**: Structured IR (JSON)

**What it parses**:
- Sources and targets (tables, flat files, XML sources)
- Mappings (transformation pipeline graphs)
- Transformations (Expression, Filter, Joiner, Lookup, Aggregator, Router, SCD, Normalizer, Rank, Sequence Generator, Sorter, Union, Custom, Java)
- Mapplets (reusable transformation groups)
- Workflows (sessions, links, conditions, timers, assignments)
- Sessions (connection overrides, pushdown config, partitioning)
- Connection objects (database, FTP, HTTP, etc.)
- Scheduler configurations

**Design decisions**:
- Deterministic XML parsing (no LLM) — Informatica XML schema is well-defined
- AST-style representation preserving all metadata
- Handles nested mapplets by recursive resolution
- Preserves session-level overrides that affect runtime behavior

---

### 2. Intermediate Representation (IR)

A normalized, source-agnostic representation of ETL pipeline logic.

```json
{
  "pipeline": {
    "id": "m_customer_dim",
    "name": "Customer Dimension Load",
    "type": "mapping",
    "complexity_score": 7.2,
    "sources": [
      {
        "id": "src_customers",
        "type": "relational",
        "connection": "ORACLE_PROD",
        "schema": "CRM",
        "table": "CUSTOMERS",
        "columns": [...]
      }
    ],
    "targets": [
      {
        "id": "tgt_dim_customer",
        "type": "relational",
        "connection": "REDSHIFT_DW",
        "schema": "ANALYTICS",
        "table": "DIM_CUSTOMER",
        "columns": [...],
        "load_type": "upsert",
        "key_columns": ["customer_id"]
      }
    ],
    "transforms": [
      {
        "id": "exp_derive_fields",
        "type": "expression",
        "inputs": ["src_customers"],
        "outputs": ["fil_active"],
        "expressions": [
          {
            "output_field": "full_name",
            "expression": "LTRIM(RTRIM(first_name)) || ' ' || LTRIM(RTRIM(last_name))",
            "datatype": "string(200)"
          },
          {
            "output_field": "customer_status",
            "expression": "IIF(ISNULL(deactivation_date), 'ACTIVE', IIF(deactivation_date > SYSDATE - 90, 'RECENT_CHURN', 'INACTIVE'))",
            "datatype": "string(20)"
          }
        ]
      },
      {
        "id": "fil_active",
        "type": "filter",
        "inputs": ["exp_derive_fields"],
        "outputs": ["lkp_region"],
        "condition": "customer_status != 'INACTIVE' AND created_date > TO_DATE('2020-01-01', 'YYYY-MM-DD')"
      },
      {
        "id": "lkp_region",
        "type": "lookup",
        "inputs": ["fil_active"],
        "outputs": ["tgt_dim_customer"],
        "lookup_table": "REF.REGION_MAPPING",
        "lookup_condition": "zip_code = IN_zip_code",
        "return_fields": ["region_name", "territory_id"],
        "default_on_miss": {"region_name": "UNKNOWN", "territory_id": -1}
      }
    ],
    "lineage": {
      "src_customers.first_name": ["tgt_dim_customer.full_name"],
      "src_customers.last_name": ["tgt_dim_customer.full_name"],
      "src_customers.deactivation_date": ["tgt_dim_customer.customer_status"],
      "REF.REGION_MAPPING.region_name": ["tgt_dim_customer.region_name"]
    }
  },
  "workflow": {
    "id": "wf_customer_dim",
    "schedule": "0 2 * * *",
    "sessions": [
      {
        "id": "s_customer_dim",
        "mapping": "m_customer_dim",
        "on_success": "s_customer_agg",
        "on_failure": "email_ops_team",
        "config": {
          "commit_interval": 10000,
          "dtm_buffer_size": "128MB"
        }
      }
    ]
  }
}
```

**Design decisions**:
- JSON format for easy manipulation and LLM consumption
- Preserves full lineage (column-level)
- Includes workflow/orchestration context alongside transformation logic
- Complexity score computed from: transform count, expression depth, lookup count, SCD presence, custom code

---

### 3. Expression Translator (Hybrid: Rules + LLM)

Converts Informatica expression language to SQL (for dbt) or PySpark (for Glue).

**Layer 1 — Deterministic rules (handles ~80% of expressions)**:

| Informatica | SQL (dbt) | PySpark |
|------------|-----------|---------|
| `IIF(cond, true, false)` | `CASE WHEN cond THEN true ELSE false END` | `when(cond, true).otherwise(false)` |
| `DECODE(val, m1, r1, m2, r2, def)` | `CASE val WHEN m1 THEN r1 WHEN m2 THEN r2 ELSE def END` | `when(col == m1, r1).when(col == m2, r2).otherwise(def)` |
| `ISNULL(x)` | `x IS NULL` | `col(x).isNull()` |
| `LTRIM(RTRIM(x))` | `TRIM(x)` | `trim(col(x))` |
| `TO_DATE(x, fmt)` | `TO_DATE(x, fmt)` | `to_date(col(x), fmt)` |
| `SUBSTR(x, s, l)` | `SUBSTRING(x, s, l)` | `substring(col(x), s, l)` |
| `LPAD(x, n, c)` | `LPAD(x, n, c)` | `lpad(col(x), n, c)` |
| `SYSDATE` | `CURRENT_TIMESTAMP` | `current_timestamp()` |
| `ADD_TO_DATE(d, 'DD', n)` | `DATEADD(day, n, d)` | `date_add(col(d), n)` |
| `REG_EXTRACT(x, pat, n)` | `REGEXP_SUBSTR(x, pat, 1, 1, 'e', n)` | `regexp_extract(col(x), pat, n)` |

**Layer 2 — LLM translation (handles ~20% — complex/nested/custom)**:
- Nested IIF/DECODE combinations (5+ levels deep)
- Custom functions (user-defined in Informatica)
- Java transformation logic
- Expressions referencing session variables or mapping parameters
- Undocumented business logic requiring intent inference

**Layer 3 — Validation feedback loop**:
- Generate both source (Informatica expression) and target (SQL) test cases
- Run against sample data
- If outputs differ, feed the diff back to LLM for self-correction
- After 3 failed attempts, flag for human review with explanation

---

### 4. Target Generators

#### dbt Generator

Produces a complete, runnable dbt project:

```
output/
├── dbt_project.yml
├── packages.yml
├── models/
│   ├── staging/
│   │   ├── _staging__sources.yml
│   │   ├── _staging__models.yml
│   │   └── stg_crm__customers.sql
│   ├── intermediate/
│   │   └── int_customers__enriched.sql
│   └── marts/
│       ├── _marts__models.yml
│       └── dim_customer.sql
├── macros/
│   ├── custom_lookups.sql
│   └── informatica_compat.sql
├── tests/
│   └── reconciliation/
│       └── recon_dim_customer.sql
├── snapshots/
│   └── snap_customer_scd2.sql
└── seeds/
    └── region_mapping.csv
```

**Conventions applied**:
- Staging/intermediate/marts layer structure (dbt best practices)
- `ref()` and `source()` for dependency management
- Schema tests auto-generated (not_null, unique, relationships)
- Custom reconciliation tests for migration validation
- Jinja macros for repeated patterns (lookups, SCD logic)

#### Glue Generator

Produces deployable AWS Glue jobs:

```
output/
├── jobs/
│   ├── customer_dim_load.py          # PySpark Glue job
│   └── customer_agg.py
├── infrastructure/
│   ├── glue_jobs.tf                  # Terraform for Glue jobs
│   ├── connections.tf                # Glue connections
│   ├── crawlers.tf                   # Glue crawlers
│   └── step_functions.tf             # Orchestration
├── tests/
│   └── test_customer_dim_load.py     # pytest for Glue job logic
└── config/
    └── job_parameters.json           # Runtime parameters
```

#### Orchestration Generator

Converts Informatica Workflows to:

| Target | Output |
|--------|--------|
| AWS Step Functions | ASL (Amazon States Language) JSON definition |
| MWAA (Managed Airflow) | Python DAG files |
| EventBridge | Rule definitions for scheduling |

---

### 5. Validation Engine

Auto-generates reconciliation artifacts for every converted mapping:

```sql
-- reconciliation/recon_dim_customer.sql
-- Compares row counts and checksums between source and target

WITH source_stats AS (
    SELECT
        COUNT(*) as row_count,
        COUNT(DISTINCT customer_id) as distinct_keys,
        SUM(CAST(revenue AS BIGINT)) as revenue_checksum
    FROM {{ source('legacy_oracle', 'customers') }}
    WHERE created_date > '2020-01-01'
),
target_stats AS (
    SELECT
        COUNT(*) as row_count,
        COUNT(DISTINCT customer_id) as distinct_keys,
        SUM(CAST(revenue AS BIGINT)) as revenue_checksum
    FROM {{ ref('dim_customer') }}
)
SELECT
    'row_count' as check_name,
    s.row_count as source_value,
    t.row_count as target_value,
    CASE WHEN s.row_count = t.row_count THEN 'PASS' ELSE 'FAIL' END as status
FROM source_stats s, target_stats t
UNION ALL
SELECT
    'revenue_checksum',
    s.revenue_checksum,
    t.revenue_checksum,
    CASE WHEN s.revenue_checksum = t.revenue_checksum THEN 'PASS' ELSE 'FAIL' END
FROM source_stats s, target_stats t
```

---

## Infrastructure & Deployment

### Runtime Environment
- **Amazon Bedrock AgentCore**: Secure agent execution, credential management via workload identities, IAM-based access control
- **Amazon Bedrock (Claude)**: LLM for expression translation, explanation generation, conversational interface
- **Amazon S3**: Stores input artifacts (XML exports), output code, reports, validation results
- **AWS Secrets Manager**: Source/target database credentials (never leave customer's account)

### Security Model
- All processing happens within the customer's AWS account
- No data leaves the customer's environment
- XML exports contain metadata only (no actual data rows)
- Credentials managed through Secrets Manager + IAM roles
- Agent has least-privilege access (read XML from S3, write output to S3, invoke Bedrock)

### Scalability
- Stateless agent design — each mapping conversion is independent
- Parallel processing: convert hundreds of mappings simultaneously
- Bedrock handles LLM scaling automatically
- S3 for artifact storage (unlimited scale)

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| XML Parser | Python (lxml/ElementTree) | Mature, fast, well-suited for Informatica XML schema |
| IR Schema | JSON Schema / Pydantic models | Type-safe, validatable, LLM-friendly |
| Expression Rules Engine | Python (custom AST walker) | Deterministic, testable, fast |
| LLM Integration | Amazon Bedrock (Claude) | AWS-native, strong code generation, tool use |
| Agent Framework | Bedrock AgentCore | AWS-native runtime, credential management |
| dbt Output | Jinja2 templates | dbt uses Jinja natively — natural fit |
| Glue Output | Python string templates | PySpark code generation |
| IaC Output | Terraform (HCL templates) | Industry standard, multi-resource |
| Testing | pytest + dbt test | Unit tests for logic, integration tests for output |
| Orchestration | Step Functions ASL / Airflow DAG templates | AWS-native options |

---

## Key Technical Risks

| Risk | Mitigation |
|------|-----------|
| Informatica XML schema varies across versions (8.x, 9.x, 10.x) | Support 10.x first (most common), add version detection and adapters |
| Custom Java transformations can't be auto-converted | Flag for human review, provide PySpark skeleton with logic description |
| Expression translation accuracy below target | Validation feedback loop catches errors; human-in-the-loop for edge cases |
| Large estates (10,000+ mappings) overwhelm LLM context | Process mappings independently; batch in parallel; only use LLM for expressions, not full mappings |
| Bedrock rate limits during large conversions | Implement queuing, retry with backoff, parallel across regions if needed |
| Generated dbt code doesn't compile | Post-generation compilation check; fix common issues automatically |
