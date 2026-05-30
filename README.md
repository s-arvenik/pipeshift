# PipeShift

**Convert Informatica PowerCenter ETL pipelines to dbt projects and AWS Step Functions.**

PipeShift is an open-source tool that parses Informatica PowerCenter XML exports and generates production-ready dbt models, source definitions, schema tests, reconciliation queries, and orchestration code.

## Quick Start

```bash
pip install pipeshift

# Analyze an export
pipeshift analyze ./my_export.xml

# Convert to dbt
pipeshift convert ./my_export.xml -o ./dbt_project/

# Batch convert a directory
pipeshift convert ./exports/ -o ./dbt_project/
```

## What It Does

```
Informatica XML → Parser → IR → Generator → dbt project
                                    ↑
                            Translator (expressions)
```

**Input:** Informatica PowerCenter XML exports (File → Export Objects in Designer)

**Output:**
- dbt SQL models (one per mapping, CTE structure)
- `_sources.yml` with connection info and column tests
- `_schema.yml` with unique/not_null constraints
- Reconciliation queries (row count validation)
- dbt snapshots (for SCD Type 2 mappings)
- AWS Step Functions ASL (from Informatica Workflows)

## Supported Transforms

| Transform | Output |
|-----------|--------|
| Source Qualifier | `{{ source() }}` CTE with optional filter |
| Expression | SQL expressions in SELECT |
| Filter | WHERE clause |
| Lookup | LEFT JOIN |
| Aggregator | GROUP BY |
| Joiner | JOIN (inner/left/right/full) |
| Router | UNION ALL of filtered CTEs |
| Union | UNION ALL |
| Sorter | Pass-through (ordering handled by target) |
| Rank | ROW_NUMBER() OVER() |
| Sequence Generator | ROW_NUMBER() + offset |
| Update Strategy | Incremental/merge config |
| Normalizer | CROSS JOIN LATERAL (UNPIVOT) |
| SCD Type 2 | dbt snapshot |

## Expression Translation

PipeShift translates Informatica expressions to SQL deterministically:

| Informatica | SQL |
|-------------|-----|
| `IIF(cond, a, b)` | `CASE WHEN cond THEN a ELSE b END` |
| `DECODE(x, a, b, c)` | `CASE WHEN x = a THEN b ELSE c END` |
| `ISNULL(x)` | `x IS NULL` |
| `NVL(x, y)` | `COALESCE(x, y)` |
| `SUBSTR(x, s, l)` | `SUBSTRING(x, s, l)` |
| `INSTR(x, y)` | `STRPOS(x, y)` |
| `LTRIM(RTRIM(x))` | `TRIM(x)` |
| `TO_INTEGER(x)` | `CAST(x AS INTEGER)` |
| `ADD_TO_DATE(x, 'DD', n)` | `DATEADD(day, n, x)` |
| `$$PARAM` | `{{ var('param') }}` |
| `:LKP.name(port)` | Subquery with `ref()` |

[Full list: 20+ functions supported](src/pipeshift/translator/__init__.py)

## Orchestration

Informatica Workflows are converted to AWS Step Functions:

| Informatica | Step Functions |
|-------------|---------------|
| Session | Glue startJobRun task |
| Command Task | Lambda invoke |
| Email Task | SNS publish |
| Worklet | Inlined sessions |
| Link conditions | Choice state / Next |
| Failure links | Catch blocks |

## Features

- **DAG-aware code generation** — Topological sort of transforms produces CTEs in correct dependency order
- **Mapplet resolution** — Reusable sub-mappings are automatically inlined
- **Batch mode** — Convert entire directories of XML exports
- **Graceful degradation** — Unsupported transforms (Java, Stored Procedures) produce TODO comments, not crashes
- **Complexity scoring** — Classifies mappings as simple/medium/complex/manual with effort estimates

## Installation

```bash
# From source
git clone https://github.com/yourusername/pipeshift.git
cd pipeshift
pip install -e ".[dev]"

# Run tests
pytest
```

Requires Python 3.11+. Dependencies: `pydantic`, `lxml`.

## Project Structure

```
src/pipeshift/
├── parser/          # Informatica XML → IR
├── ir/              # Intermediate Representation (Pydantic models)
├── translator/      # Informatica expressions → SQL
├── generator/       # IR → dbt project files
├── orchestration/   # Workflows → Step Functions ASL
├── analyzer.py      # Estate analysis and reporting
├── scorer.py        # Complexity scoring
└── cli.py           # Command-line interface
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. Key points:

- Every new translation rule needs a test
- Parser changes must not break existing XML fixtures
- The IR schema is the contract — parser and generator never import each other

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions, data flow, and module responsibilities.

## Limitations

- No mapplet nesting (single level only)
- No custom Java transform conversion (flagged as TODO)
- Single folder per XML export
- No Glue PySpark output yet (dbt SQL only)
- No MWAA/Airflow DAG generation (Step Functions only)

## License

Apache 2.0 — see [LICENSE](LICENSE).
