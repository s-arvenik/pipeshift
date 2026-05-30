# Architecture Guide

This document describes how PipeShift is structured, how data flows through the system, and the design decisions that guide development.

## Data Flow

```
Informatica XML → Parser → IR (Intermediate Representation) → Generator → dbt project
                                      ↑
                              Translator (expressions)
```

Each stage is independent. The IR is the contract between them.

## Module Responsibilities

### `pipeshift/parser/informatica_xml.py`
- **Input**: Informatica PowerCenter XML export file (`.xml`)
- **Output**: `Repository` (IR model)
- **Rules**:
  - Deterministic only. No LLM, no heuristics, no guessing.
  - If the XML has it, we capture it. If it doesn't, we leave the field as None.
  - Cross-references repo-level sources into mapping-level sources for connection/schema enrichment.
  - CONNECTOR elements define the transform DAG (inputs/outputs).

### `pipeshift/ir/schema.py`
- **Purpose**: The single source of truth for data structures between parser and generator.
- **Rules**:
  - Pydantic BaseModel only. No logic, no methods, no computed properties.
  - Source-agnostic: nothing in the IR should be Informatica-specific or dbt-specific.
  - Use `Optional` for anything that might not be present. Use `properties: Dict` as escape hatch for unmodeled metadata.
  - Adding a new field is always safe (defaults to None/empty). Removing a field is a breaking change.

### `pipeshift/translator/__init__.py`
- **Input**: Informatica expression string (e.g., `"IIF(ISNULL(X), 'N/A', X)"`)
- **Output**: SQL expression string (e.g., `"CASE WHEN (X IS NULL) THEN 'N/A' ELSE X END"`)
- **Rules**:
  - Two layers: deterministic rules first, LLM fallback later (not yet implemented).
  - Paren-aware parsing for any function that takes arguments (never use simple regex for nested content).
  - Combined patterns (LTRIM(RTRIM(x))→TRIM(x)) must appear BEFORE individual patterns in the rules list.
  - If a function can't be translated, pass it through unchanged (don't error, don't hallucinate).
  - Every new rule must have a corresponding test.

### `pipeshift/generator/__init__.py`
- **Input**: `Repository` (IR model)
- **Output**: dbt project files on disk
- **Rules**:
  - One model file per mapping. CTE-based structure.
  - Transform processing order: Source → Joiner → Expression → Filter → Aggregator → Lookup → final SELECT.
  - Use `{{ source('connection', 'table') }}` for all table references.
  - Never hardcode connection names — always derive from IR.
  - `_find_all_transforms()` not `_find_transform()` — mappings can have multiples of any type.

### `pipeshift/cli.py`
- **Purpose**: User-facing entry point.
- **Rules**:
  - Thin wrapper. All logic lives in parser/translator/generator.
  - Two commands: `analyze` (read-only report) and `convert` (writes files).
  - Exit code 0 on success, 1 on error. Errors go to stderr.

## Design Principles

1. **IR is the contract.** Parser produces it. Generator consumes it. They never import each other.

2. **Deterministic first.** Every translation rule must be provably correct for all inputs it matches. If unsure, don't match — leave for LLM fallback.

3. **Fail gracefully.** An unsupported transform type should produce a comment in the SQL (`-- TODO: unsupported transform: Java`), not crash the pipeline.

4. **One mapping = one model.** Don't try to merge or split mappings. The customer's structure is preserved.

5. **Test against real XML.** Every new feature needs a sample XML that exercises it. Synthetic is fine, but must match real Informatica export structure.

## Transform Processing Pipeline (in generator)

```
_build_model_sql(mapping)
  │
  ├─ source_data CTE ← Source Qualifier (with Source Filter if present)
  │
  ├─ joined CTE ← Joiner (if present, joins additional sources)
  │
  ├─ transformed CTE ← Expression transforms (computed fields)
  │
  ├─ filtered CTE ← Filter transform (WHERE clause)
  │
  ├─ aggregated CTE ← Aggregator (GROUP BY + aggregate functions)
  │
  ├─ final/with_lookup CTE ← Lookup (LEFT JOIN to reference tables)
  │
  └─ SELECT * FROM <last_cte>
```

This order matches the typical Informatica execution flow. If a transform type is absent, its CTE is skipped and the next stage reads from the previous one.

## Known Limitations (MVP)

- Single folder per XML export (first FOLDER element used)
- `SELECT *` in CTEs can produce duplicate columns when joining
- No nested mapplet resolution (single level only)
- No support for multiple Source Qualifiers feeding into one mapping (multi-source joins should use Joiner)
- YAML output uses string concatenation (no escaping for special characters in descriptions)

## Adding a New Source Format (future)

Create a new parser module (e.g., `pipeshift/parser/datastage_dsx.py`) that:
1. Accepts the source format file path
2. Returns a `Repository` object
3. Uses the same IR schema — no changes needed downstream

The generator and translator work unchanged because they only know about the IR.
