# Copyright 2026 PipeShift Contributors
# SPDX-License-Identifier: Apache-2.0
"""Generate dbt project artifacts from PipeShift IR."""

import re
from pathlib import Path
from typing import Dict, List, Optional

from pipeshift.ir.schema import (
    Mapping,
    Repository,
    SCDType,
    Source,
    Transform,
    TransformType,
)
from pipeshift.translator import translate_expression


def generate_dbt_project(repo: Repository, output_dir: Path) -> List[Path]:
    """Generate a complete dbt project from a parsed repository.

    Returns list of generated file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: List[Path] = []

    # Build mapping target → mapping name lookup for ref() resolution
    # If mapping A writes to target X, and mapping B reads from source X,
    # then B should use ref('m_a') instead of source()
    target_to_mapping: Dict[str, str] = {}
    for m in repo.mappings:
        for t in m.targets:
            target_to_mapping[t.name] = _sanitize_name(m.name)

    # dbt_project.yml
    generated.append(_write_dbt_project_yml(repo, output_dir))

    # sources.yml
    if repo.sources:
        generated.append(_write_sources_yml(repo, output_dir))

    # Models and Snapshots
    models_dir = output_dir / "models"
    models_dir.mkdir(exist_ok=True)
    snapshots_dir = output_dir / "snapshots"

    for mapping in repo.mappings:
        # Check if mapping contains SCD Type 2 → generate snapshot instead of model
        scd_transforms = [
            t for t in mapping.transforms
            if t.type == TransformType.SCD
            and t.scd_config
            and t.scd_config.scd_type == SCDType.TYPE_2
        ]
        if scd_transforms:
            snapshots_dir.mkdir(exist_ok=True)
            snap_path = _write_snapshot(mapping, scd_transforms[0], snapshots_dir)
            generated.append(snap_path)
        else:
            model_path = _write_model(mapping, models_dir, target_to_mapping)
            generated.append(model_path)

    # schema.yml (model tests/docs)
    if repo.mappings:
        generated.append(_write_schema_yml(repo, models_dir))

    # Reconciliation tests
    tests_dir = output_dir / "tests" / "reconciliation"
    for mapping in repo.mappings:
        if mapping.sources and mapping.targets:
            tests_dir.mkdir(parents=True, exist_ok=True)
            recon_path = _write_reconciliation_test(mapping, tests_dir)
            generated.append(recon_path)

    return generated


def _write_dbt_project_yml(repo: Repository, output_dir: Path) -> Path:
    folder = repo.folder or "pipeshift"
    project_name = _sanitize_name(folder)
    content = f"""name: '{project_name}'
version: '1.0.0'
config-version: 2

profile: '{project_name}'

model-paths: ["models"]
test-paths: ["tests"]
macro-paths: ["macros"]

target-path: "target"
clean-targets:
  - "target"
  - "dbt_packages"
"""
    path = output_dir / "dbt_project.yml"
    path.write_text(content)
    return path


def _write_sources_yml(repo: Repository, output_dir: Path) -> Path:
    models_dir = output_dir / "models"
    models_dir.mkdir(exist_ok=True)

    # Group sources by connection/schema
    groups: Dict[str, List[Source]] = {}
    for src in repo.sources:
        key = src.connection or "default"
        groups.setdefault(key, []).append(src)

    lines = ["version: 2", "", "sources:"]
    for conn_name, sources in groups.items():
        schema = sources[0].schema_name or conn_name
        lines.append(f"  - name: {_sanitize_name(conn_name)}")
        lines.append(f"    schema: {schema}")
        lines.append("    tables:")
        for src in sources:
            lines.append(f"      - name: {src.name.lower()}")
            if src.columns:
                lines.append("        columns:")
                for col in src.columns:
                    lines.append(f"          - name: {col.name.lower()}")
                    if not col.nullable:
                        lines.append("            tests:")
                        lines.append("              - not_null")

    content = "\n".join(lines) + "\n"
    path = models_dir / "_sources.yml"
    path.write_text(content)
    return path


def _write_reconciliation_test(mapping: Mapping, tests_dir: Path) -> Path:
    """Generate a reconciliation test comparing source row count to target."""
    model_name = _sanitize_name(mapping.name)
    src = mapping.sources[0]
    src_conn = _sanitize_name(src.connection or "default")
    src_table = (src.table or src.name).lower()

    sql = f"""-- Reconciliation test for {model_name}
-- Validates row count between source and converted model

WITH source_count AS (
    SELECT COUNT(*) AS cnt
    FROM {{{{ source('{src_conn}', '{src_table}') }}}}
),
target_count AS (
    SELECT COUNT(*) AS cnt
    FROM {{{{ ref('{model_name}') }}}}
)
SELECT
    'row_count' AS check_name,
    s.cnt AS source_value,
    t.cnt AS target_value,
    ABS(s.cnt - t.cnt) AS difference
FROM source_count s, target_count t
WHERE s.cnt != t.cnt
"""
    path = tests_dir / f"recon_{model_name}.sql"
    path.write_text(sql)
    return path


def _write_snapshot(mapping: Mapping, scd_transform: Transform, snapshots_dir: Path) -> Path:
    """Generate a dbt snapshot file from an SCD Type 2 mapping."""
    scd = scd_transform.scd_config
    snapshot_name = _sanitize_name(mapping.name).replace("m_", "snap_")
    source_qual = _find_transform(mapping, TransformType.SOURCE_QUALIFIER)
    source_ref = _get_source_ref(mapping, source_qual)

    # Determine strategy: if we have an effective date field, use timestamp; otherwise check
    if scd.effective_date_field:
        strategy = "timestamp"
        strategy_config = f"updated_at='{scd.effective_date_field.lower()}',"
    else:
        strategy = "check"
        check_cols = ", ".join(f"'{c.lower()}'" for c in scd.tracked_columns)
        strategy_config = f"check_cols=[{check_cols}],"

    unique_key = ", ".join(f"'{k.lower()}'" for k in scd.key_columns)

    if len(scd.key_columns) == 1:
        unique_key_config = f"        unique_key='{scd.key_columns[0].lower()}',"
    else:
        unique_key_config = f"        unique_key=[{unique_key}],"

    lines = [
        f"{{% snapshot {snapshot_name} %}}",
        "",
        "{{",
        "    config(",
        f"        target_schema='snapshots',",
        unique_key_config,
        f"        strategy='{strategy}',",
        f"        {strategy_config}",
        "    )",
        "}}",
        "",
        "SELECT",
    ]

    # Select tracked columns + key columns + updated_at column (needed for timestamp strategy)
    all_cols = list(scd.key_columns) + list(scd.tracked_columns)
    if scd.effective_date_field:
        all_cols.append(scd.effective_date_field)
    # Deduplicate while preserving order
    seen = set()
    select_cols = []
    for c in all_cols:
        if c not in seen:
            select_cols.append(c.lower())
            seen.add(c)

    lines.append("    " + ",\n    ".join(select_cols))
    lines.append(f"FROM {source_ref}")
    lines.append("")
    lines.append(f"{{% endsnapshot %}}")

    content = "\n".join(lines) + "\n"
    path = snapshots_dir / f"{snapshot_name}.sql"
    path.write_text(content)
    return path


def _write_model(mapping: Mapping, models_dir: Path, target_to_mapping: Dict[str, str]) -> Path:
    """Generate a dbt SQL model from a mapping."""
    model_name = _sanitize_name(mapping.name)
    sql = _build_model_sql(mapping, target_to_mapping)

    # Add TODO comments for unsupported transforms
    unsupported = [
        t for t in mapping.transforms
        if t.type in (TransformType.CUSTOM, TransformType.JAVA, TransformType.STORED_PROCEDURE)
    ]
    if unsupported:
        header = "-- ⚠️  MANUAL REVIEW REQUIRED\n"
        for t in unsupported:
            header += f"-- TODO: Unsupported transform '{t.name}' (type: {t.type.value})\n"
        header += "\n"
        sql = header + sql

    path = models_dir / f"{model_name}.sql"
    path.write_text(sql)
    return path


def _build_model_sql(mapping: Mapping, target_to_mapping: Optional[Dict[str, str]] = None) -> str:
    """Build the SQL for a dbt model from a mapping's transforms."""
    # Find entry points (Source Qualifiers)
    source_quals = [t for t in mapping.transforms if t.type == TransformType.SOURCE_QUALIFIER]
    
    # Sort transforms topologically
    sorted_transforms = _topological_sort(mapping.transforms)
    
    # Determine primary source reference for the dbt model
    # (Used for simple models or as the starting point for CTEs)
    primary_sq = source_quals[0] if source_quals else None
    source_ref = _get_source_ref(mapping, primary_sq, target_to_mapping)

    # Build CTEs for complex mappings, or a simple SELECT for basic ones
    if len(mapping.transforms) > 1 or (primary_sq and primary_sq.properties.get("Source Filter")):
        sql = _build_dag_model(mapping, sorted_transforms, target_to_mapping)
    else:
        filter_t = _find_transform(mapping, TransformType.FILTER)
        sql = _build_simple_model(source_ref, primary_sq, filter_t)

    # Wrap with incremental config if Update Strategy present
    update_strat = _find_transform(mapping, TransformType.UPDATE_STRATEGY)
    if update_strat:
        sql = _wrap_incremental(sql, update_strat, mapping)

    return sql


def _topological_sort(transforms: List[Transform]) -> List[Transform]:
    """Sort transformations based on their input/output dependencies."""
    visited = set()
    stack = []
    
    transform_map = {t.name: t for t in transforms}
    
    def visit(t_name):
        if t_name in visited or t_name not in transform_map:
            return
        visited.add(t_name)
        t = transform_map[t_name]
        for inp in t.inputs:
            visit(inp)
        stack.append(t)

    for t in transforms:
        visit(t.name)
        
    return stack


def _build_dag_model(
    mapping: Mapping,
    sorted_transforms: List[Transform],
    target_to_mapping: Optional[Dict[str, str]] = None
) -> str:
    """Build SQL model using a topological DAG of CTEs."""
    lines: List[str] = []
    cte_names: Dict[str, str] = {}  # transform_name -> cte_name
    
    # Pre-populate mapping sources for entry point resolution
    source_by_sq: Dict[str, str] = {}
    for src in mapping.sources:
        source_by_sq[f"SQ_{src.name}"] = _get_source_ref(mapping, None, target_to_mapping)

    for i, t in enumerate(sorted_transforms):
        cte_name = "normalized" if t.type == TransformType.NORMALIZER else ("t_" + _sanitize_name(t.name))
        cte_names[t.name] = cte_name
        
        is_first = (i == 0)
        is_last = (i == len(sorted_transforms) - 1)
        prefix = "WITH " if is_first else ""
        
        lines.append(f"{prefix}{cte_name} AS (")

        # Helper: resolve previous CTE reference safely
        prev_ref = cte_names.get(t.inputs[0], "unknown") if t.inputs else "unknown"
        
        # Build SELECT clause (skipped for Union/Router as they define their own structure)
        if t.type not in (TransformType.UNION, TransformType.ROUTER):
            select_lines = []
            if t.output_ports:
                # Map expressions to output ports
                expr_map = {e.output_field: e.expression for e in t.expressions}
                for port in t.output_ports:
                    if port in expr_map:
                        sql_expr = translate_expression(expr_map[port])
                        select_lines.append(f"        {sql_expr} AS {port.lower()}")
                    else:
                        select_lines.append(f"        {port.lower()}")
            else:
                select_lines.append("        *")

            # Add analytic functions for Rank and Sequence Generator
            if t.type == TransformType.RANK:
                group_by = t.properties.get("Group By Ports", "")
                order_by = t.properties.get("Order By Ports", t.properties.get("Rank Port", "1"))
                group_clause = f"PARTITION BY {group_by.lower()}" if group_by else ""
                order_clause = f"ORDER BY {order_by.lower()}" if order_by else "ORDER BY 1"
                select_lines.append(f"        ROW_NUMBER() OVER({group_clause} {order_clause}) AS rank_num")
            elif t.type == TransformType.SEQUENCE_GENERATOR:
                start_val = int(t.properties.get("Start Value", "1"))
                offset = start_val - 1
                offset_str = f" + {offset}" if offset != 0 else ""
                select_lines.append(f"        ROW_NUMBER() OVER(ORDER BY 1){offset_str} AS generated_id")
                
            lines.append("    SELECT")
            lines.append(",\n".join(select_lines))
        
        # Build FROM clause
        if t.type == TransformType.SOURCE_QUALIFIER:
            src_ref = source_by_sq.get(t.name, _get_source_ref(mapping, t, target_to_mapping))
            lines.append(f"    FROM {src_ref}")
            if t.properties.get("Source Filter"):
                filter_sql = translate_expression(t.properties["Source Filter"])
                lines.append(f"    WHERE {filter_sql}")
        
        elif t.type == TransformType.JOINER:
            # Joiner logic: first input is 'detail', second is 'master'
            if len(t.inputs) >= 2:
                left_cte = cte_names.get(t.inputs[0], t.inputs[0])
                right_input = t.inputs[1]
                right_ref = cte_names.get(right_input, source_by_sq.get(right_input, right_input))
                
                join_sql = _build_joiner_sql(t, left_cte, right_ref)
                lines.append(join_sql)
            else:
                lines.append(f"    FROM {prev_ref}")
        
        elif t.type == TransformType.LOOKUP:
            # Lookup logic: JOIN to lookup source
            if t.lookup_config:
                lkp = t.lookup_config
                lkp_table = lkp.lookup_source.lower()
                lkp_parts = lkp_table.split(".")
                if len(lkp_parts) == 2:
                    lkp_ref = f"{{{{ source('{_sanitize_name(lkp_parts[0])}', '{lkp_parts[1]}') }}}}"
                else:
                    lkp_ref = f"{{{{ source('default', '{lkp_table}') }}}}"

                join_cond = _translate_lookup_condition(lkp.lookup_condition)
                
                lines.append(f"    FROM {prev_ref} f")
                lines.append(f"    LEFT JOIN {lkp_ref} lkp")
                lines.append(f"        ON {join_cond}")
            else:
                lines.append(f"    FROM {prev_ref}")

        elif t.type == TransformType.ROUTER:
            if t.router_groups:
                # Pop the open CTE line for the router name to prepend route CTEs
                lines.pop()
                for grp in t.router_groups:
                    route_name = f"route_{_sanitize_name(grp.name)}"
                    condition = translate_expression(grp.condition)
                    lines.append(f"{route_name} AS (")
                    lines.append(f"    SELECT * FROM {prev_ref} WHERE {condition}")
                    lines.append("),")
                    lines.append("")
                # Re-open the main router name as a UNION of the routes
                lines.append(f"{cte_name} AS (")
                union_parts = [f"    SELECT * FROM route_{_sanitize_name(g.name)}" for g in t.router_groups]
                lines.append("\n    UNION ALL\n".join(union_parts))
            else:
                lines.append(f"    FROM {prev_ref}")

        elif t.type == TransformType.UNION:
            # Union logic: UNION ALL of all inputs
            union_parts = []
            for inp in t.inputs:
                ref = cte_names.get(inp, source_by_sq.get(inp, inp))
                union_parts.append(f"    SELECT * FROM {ref}")
            lines.append("\n    UNION ALL\n".join(union_parts))
            
        elif t.type == TransformType.AGGREGATOR:
            agg_sql = _build_aggregator_sql(t, prev_ref)
            lines.append(agg_sql)

        elif t.type in (TransformType.RANK, TransformType.SEQUENCE_GENERATOR):
            if t.type == TransformType.SEQUENCE_GENERATOR and prev_ref == "unknown":
                # Standalone sequence generator — use (SELECT 1) as dummy source
                lines.append("    FROM (SELECT 1) AS _seq")
            else:
                lines.append(f"    FROM {prev_ref}")

        elif t.type == TransformType.NORMALIZER:
            norm_cols = t.properties.get("Normalize Columns", "")
            if norm_cols:
                cols = [c.strip().lower() for c in norm_cols.split(",") if c.strip()]
                values_list = ", ".join(f"('{c}', {c})" for c in cols)
                lines.append(f"    FROM {prev_ref}")
                lines.append(f"    CROSS JOIN LATERAL (VALUES {values_list}) AS unpivoted(column_name, column_value)")
            else:
                lines.append(f"    FROM {prev_ref} -- TODO: Normalizer columns not specified")

        else:
            # Single-input transforms (Filter, Expression, Sorter, etc.)
            lines.append(f"    FROM {prev_ref}")
            if t.type == TransformType.FILTER and t.filter_condition:
                lines.append(f"    WHERE {translate_expression(t.filter_condition)}")

        # Close the CTE
        if is_last:
            lines.append(")")
        else:
            lines.append("),")
        lines.append("")

    last_cte = cte_names[sorted_transforms[-1].name]
    lines.append(f"SELECT * FROM {last_cte}")
    
    return "\n".join(lines)


def _build_joiner_sql(t: Transform, left_ref: str, right_ref: str) -> str:
    """Helper to build JOIN clause for Joiner transform."""
    join_config = t.join_config
    join_type_sql = "INNER JOIN"
    if join_config:
        jt = join_config.join_type.value if join_config.join_type else "inner"
        join_type_map = {
            "inner": "INNER JOIN",
            "left_outer": "LEFT JOIN",
            "right_outer": "RIGHT JOIN",
            "full_outer": "FULL OUTER JOIN",
        }
        join_type_sql = join_type_map.get(jt, "INNER JOIN")

    condition = "1 = 1"
    if join_config and join_config.join_condition:
        condition = translate_expression(join_config.join_condition)
    elif t.properties.get("Join Condition"):
        condition = translate_expression(t.properties["Join Condition"])

    return f"    FROM {left_ref} a\n    {join_type_sql} {right_ref} b\n        ON {condition}"


def _build_aggregator_sql(t: Transform, prev_ref: str) -> str:
    """Helper to build GROUP BY clause for Aggregator transform."""
    group_by_str = t.properties.get("Group By Ports", "")
    if group_by_str:
        group_by_fields = [f.strip().lower() for f in group_by_str.split(",") if f.strip()]
        return f"    FROM {prev_ref}\n    GROUP BY {', '.join(group_by_fields)}"
    return f"    FROM {prev_ref}"


def _build_simple_model(
    source_ref: str,
    source_qual: Optional[Transform],
    filter_t: Optional[Transform],
) -> str:
    lines = [f"SELECT *", f"FROM {source_ref}"]
    if source_qual and source_qual.properties.get("Source Filter"):
        filter_sql = translate_expression(source_qual.properties["Source Filter"])
        lines.append(f"WHERE {filter_sql}")
    if filter_t and filter_t.filter_condition:
        keyword = "AND" if source_qual and source_qual.properties.get("Source Filter") else "WHERE"
        filter_sql = translate_expression(filter_t.filter_condition)
        lines.append(f"{keyword} {filter_sql}")
    return "\n".join(lines) + "\n"


def _write_schema_yml(repo: Repository, models_dir: Path) -> Path:
    lines = ["version: 2", "", "models:"]
    # Build a lookup of repo-level targets by name for key column resolution
    repo_target_keys: Dict[str, List[str]] = {}
    for t in repo.targets:
        if t.key_columns:
            repo_target_keys[t.name] = t.key_columns

    for mapping in repo.mappings:
        model_name = _sanitize_name(mapping.name)
        desc = mapping.description or ""
        lines.append(f"  - name: {model_name}")
        if desc:
            lines.append(f"    description: \"{desc}\"")

        # Find key columns: check mapping targets first, then match by name in repo targets
        key_columns: List[str] = []
        for mt in mapping.targets:
            if mt.key_columns:
                key_columns = mt.key_columns
                break
            # Look up in repo-level targets by name
            if mt.name in repo_target_keys:
                key_columns = repo_target_keys[mt.name]
                break

        if key_columns:
            lines.append("    columns:")
            for key_col in key_columns:
                lines.append(f"      - name: {key_col.lower()}")
                lines.append("        tests:")
                lines.append("          - unique")
                lines.append("          - not_null")

    content = "\n".join(lines) + "\n"
    path = models_dir / "_schema.yml"
    path.write_text(content)
    return path


def _wrap_incremental(sql: str, update_strat: Transform, mapping: Mapping) -> str:
    """Wrap model SQL with dbt incremental materialization config.

    Update Strategy in Informatica determines INSERT/UPDATE/DELETE behavior.
    In dbt, this maps to an incremental model with merge strategy.
    """
    # Determine unique key from mapping targets
    unique_keys = []
    for t in mapping.targets:
        if t.key_columns:
            unique_keys = [k.lower() for k in t.key_columns]
            break

    unique_key_str = f"'{unique_keys[0]}'" if len(unique_keys) == 1 else str(unique_keys)

    config = (
        "{{\n"
        "    config(\n"
        "        materialized='incremental',\n"
        f"        unique_key={unique_key_str},\n"
        "        incremental_strategy='merge',\n"
        "    )\n"
        "}}\n\n"
    )
    return config + sql


def _get_source_ref(
    mapping: Mapping,
    source_qual: Optional[Transform],
    target_to_mapping: Optional[Dict[str, str]] = None,
) -> str:
    """Determine the dbt source() or ref() reference for the mapping's primary source."""
    if source_qual and source_qual.properties.get("Sql Query"):
        return f"({source_qual.properties['Sql Query']})"

    # Use mapping-level sources (now enriched with connection/schema from repo sources)
    if mapping.sources:
        src = mapping.sources[0]
        # Check if this source is another mapping's target → use ref()
        if target_to_mapping and src.name in target_to_mapping:
            ref_model = target_to_mapping[src.name]
            # Don't self-reference
            if ref_model != _sanitize_name(mapping.name):
                return f"{{{{ ref('{ref_model}') }}}}"
        conn = _sanitize_name(src.connection or "default")
        table = src.table or src.name
        return f"{{{{ source('{conn}', '{table.lower()}') }}}}"

    # Fallback: derive from source qualifier name
    if source_qual:
        table_name = source_qual.name.replace("SQ_", "").lower()
        return f"{{{{ source('default', '{table_name}') }}}}"

    return "{{ source('default', 'unknown') }}"


def _find_transform(mapping: Mapping, t_type: TransformType) -> Optional[Transform]:
    """Find the first transform of a given type in the mapping."""
    for t in mapping.transforms:
        if t.type == t_type:
            return t
    return None


def _find_all_transforms(mapping: Mapping, t_type: TransformType) -> List[Transform]:
    """Find all transforms of a given type in the mapping."""
    return [t for t in mapping.transforms if t.type == t_type]


def _translate_lookup_condition(condition: str) -> str:
    """Translate Informatica lookup condition to SQL JOIN condition.

    Handles single and compound conditions (AND-separated).
    e.g., 'ZIP_CODE = IN_ZIP_CODE' → 'lkp.zip_code = f.zip_code'
    e.g., 'A = IN_A AND B = IN_B' → 'lkp.a = f.a AND lkp.b = f.b'
    e.g., 'CUSTOMER_ID = IN_CUSTOMER_ID AND IS_CURRENT = 'Y'' → 'lkp.customer_id = f.customer_id AND lkp.is_current = 'Y''
    """
    if not condition or not condition.strip():
        return "1 = 1  -- TODO: lookup condition not specified"

    # Split on AND for compound conditions
    clauses = re.split(r'\s+AND\s+', condition, flags=re.IGNORECASE)
    translated = []
    for clause in clauses:
        # Find the = sign (but not >= or <=)
        eq_match = re.search(r'(?<![<>!])=(?!=)', clause)
        if eq_match:
            lkp_field = clause[:eq_match.start()].strip().lower()
            in_field = clause[eq_match.end():].strip()
            # Check if right side is a literal (quoted string or number)
            if in_field.startswith("'") or in_field.startswith('"') or in_field.replace('.', '').isdigit():
                translated.append(f"lkp.{lkp_field} = {in_field}")
            else:
                # Remove IN_ prefix if present
                in_lower = in_field.lower()
                if in_lower.startswith("in_"):
                    in_lower = in_lower[3:]
                translated.append(f"lkp.{lkp_field} = f.{in_lower}")
        else:
            translated.append(clause.strip())
    return " AND ".join(translated)


def _sanitize_name(name: str) -> str:
    """Convert a name to a valid dbt identifier."""
    return name.lower().replace(" ", "_").replace("-", "_")
