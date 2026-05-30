# Changelog

## 0.1.0 (2026-05-30)

Initial open-source release.

### Features

- **Parser**: Informatica PowerCenter XML → IR with support for all 14 transform types
- **Translator**: 20+ Informatica expression functions → SQL (deterministic, paren-aware)
- **Generator**: IR → dbt project (models, sources, schema, reconciliation, snapshots)
- **Orchestration**: Workflows → AWS Step Functions ASL (sessions, commands, emails, worklets)
- **CLI**: `pipeshift analyze` and `pipeshift convert` commands
- **Batch mode**: Convert entire directories of XML exports
- **Mapplet resolution**: Reusable sub-mappings automatically inlined
- **DAG-aware generation**: Topological sort produces CTEs in correct dependency order
- **Complexity scoring**: Classifies mappings as simple/medium/complex/manual
- **Graceful degradation**: Unsupported transforms produce TODO comments, not crashes
