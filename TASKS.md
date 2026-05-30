# PipeShift — Master Task List

## Status: 153 tests passing | Phases 0-3 complete | Phase 4 in progress

---

## Phase 0: Foundation ✅ COMPLETE

- ✅ Project scaffolding (pyproject.toml, src layout, tests)
- ✅ IR schema (Pydantic models)
- ✅ Informatica PowerCenter XML parser
- ✅ Sample XML exports (4 files)
- ✅ Architecture review and bug fixes
- ✅ ARCHITECTURE.md and CONTRIBUTING.md

---

## Phase 1: Analyzer MVP ✅ COMPLETE

- ✅ CLI (`pipeshift analyze` / `pipeshift convert`)
- ✅ Inventory extraction
- ✅ Complexity scoring (simple/medium/complex/manual)
- ✅ Dependency graph (inter-mapping via shared sources/targets)
- ✅ HTML report generation (`--html`)
- ✅ JSON output mode (`--json`)

---

## Phase 2: Expression Translation Engine ✅ COMPLETE

- ✅ IIF → CASE WHEN (nested)
- ✅ DECODE → CASE (including DECODE(TRUE,...))
- ✅ ISNULL, NVL/NVL2 (paren-aware, handles nested functions)
- ✅ SYSDATE, SESSSTARTTIME → CURRENT_TIMESTAMP
- ✅ SUBSTR → SUBSTRING, INSTR → STRPOS
- ✅ LTRIM/RTRIM (standalone + combined → TRIM)
- ✅ TO_INTEGER/TO_BIGINT/TO_FLOAT/TO_DECIMAL → CAST
- ✅ ADD_TO_DATE → DATEADD (DD/MM/YY/HH)
- ✅ REG_REPLACE → REGEXP_REPLACE, REG_MATCH → REGEXP_LIKE
- ✅ TO_DATE/TO_CHAR format string translation (RR→YY, RRRR→YYYY, HH→HH24)
- ✅ $$PARAM → {{ var('param_name') }}
- ✅ :LKP.lookup_name(port) → subquery with ref()
- ✅ Tier 2: CUME→SUM, MOVINGAVG→AVG, MOVINGSUM→SUM
- ✅ Confidence scoring (HIGH/MEDIUM/LOW)
- ⬜ Validation harness (500+ expression pairs) — needs real data

---

## Phase 3: Converter MVP ✅ COMPLETE

### All 13 transform types:
- ✅ Source Qualifier → source() CTE
- ✅ Expression → SQL in SELECT
- ✅ Filter → WHERE clause
- ✅ Lookup → LEFT JOIN
- ✅ Aggregator → GROUP BY
- ✅ Joiner → JOIN
- ✅ Router → UNION ALL of filtered CTEs
- ✅ Union → UNION ALL
- ✅ SCD Type 2 → dbt snapshot (timestamp/check strategy)
- ✅ Rank → ROW_NUMBER() OVER()
- ✅ Sequence Generator → ROW_NUMBER() + offset
- ✅ Update Strategy → incremental/merge config
- ✅ Normalizer → UNPIVOT (CROSS JOIN LATERAL)

### dbt output:
- ✅ dbt_project.yml
- ✅ _sources.yml with column tests
- ✅ _schema.yml with unique/not_null
- ✅ Model SQL with CTE structure
- ✅ Snapshots for SCD2
- ✅ ref() between models (inter-mapping dependencies)
- ✅ Reconciliation tests (row count validation)

### Orchestration:
- ✅ Workflow → Step Functions ASL JSON
- ✅ Sessions → Glue startJobRun tasks
- ✅ Email → SNS publish
- ✅ Failure links → Catch blocks

---

## Phase 4: Validate & Ship 🔧 IN PROGRESS

- ✅ Graceful error handling (file not found, malformed XML, unsupported transforms → TODO)
- ✅ Dockerfile for containerized deployment
- ✅ .gitignore
- ⬜ Design partner validation (needs real Informatica exports)
- ⬜ AWS Partner Network membership
- ⬜ Bedrock AgentCore packaging
- ⬜ AWS Marketplace listing
- ⬜ Interactive demos (Storylane)
- ⬜ Launch blog post

---

## Phase 5-8: Future ⬜ NOT STARTED

- Phase 5: Scale (batch processing, Glue PySpark output, MWAA DAGs)
- Phase 6: Source expansion (DataStage, SSIS, Talend parsers)
- Phase 7: Enterprise features (multi-user, audit trail, custom rules)
- Phase 8: Intelligence (self-improving, partner portal, lineage viz)
