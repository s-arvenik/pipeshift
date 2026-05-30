# Copyright 2026 PipeShift Contributors
# SPDX-License-Identifier: Apache-2.0
"""Parser for Informatica PowerCenter XML exports → PipeShift IR."""

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union
from xml.etree import ElementTree as ET

from pipeshift.ir.schema import (
    Column,
    Connection,
    FieldExpression,
    LookupConfig,
    Mapping,
    Repository,
    RouterGroup,
    SCDConfig,
    SCDType,
    Source,
    Target,
    Transform,
    TransformType,
    Workflow,
    WorkflowLink,
    WorkflowLinkType,
    WorkflowTask,
    SessionConfig,
)

# Map Informatica transformation type strings to our enum
_TRANSFORM_TYPE_MAP: Dict[str, TransformType] = {
    "Source Qualifier": TransformType.SOURCE_QUALIFIER,
    "Expression": TransformType.EXPRESSION,
    "Filter": TransformType.FILTER,
    "Joiner": TransformType.JOINER,
    "Lookup": TransformType.LOOKUP,
    "Lookup Procedure": TransformType.LOOKUP,
    "Aggregator": TransformType.AGGREGATOR,
    "Router": TransformType.ROUTER,
    "Union": TransformType.UNION,
    "Sorter": TransformType.SORTER,
    "Rank": TransformType.RANK,
    "Normalizer": TransformType.NORMALIZER,
    "Sequence Generator": TransformType.SEQUENCE_GENERATOR,
    "Sequence": TransformType.SEQUENCE_GENERATOR,
    "Slowly Changing Dimension": TransformType.SCD,
    "Update Strategy": TransformType.UPDATE_STRATEGY,
    "Custom Transformation": TransformType.CUSTOM,
    "Java": TransformType.JAVA,
    "Stored Procedure": TransformType.STORED_PROCEDURE,
    "SQL Transformation": TransformType.CUSTOM,
    "Transaction Control": TransformType.CUSTOM,
    "Input Transformation": TransformType.CUSTOM,
    "Output Transformation": TransformType.CUSTOM,
    "Target Definition": TransformType.TARGET,
}


def parse_file(path: Union[str, Path]) -> Repository:
    """Parse an Informatica PowerCenter XML export file into IR."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"XML file not found: {path}")

    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise ValueError(f"Malformed XML in {path}: {e}")

    root = tree.getroot()
    if root.tag != "POWERMART":
        raise ValueError(
            f"Invalid Informatica export: expected root element 'POWERMART', "
            f"got '{root.tag}'"
        )

    repo_el = root.find("REPOSITORY")
    if repo_el is None:
        raise ValueError("No REPOSITORY element found in XML")

    folder_el = repo_el.find("FOLDER")
    folder_name = folder_el.get("NAME", "") if folder_el is not None else None

    container = folder_el if folder_el is not None else repo_el

    sources = [_parse_source(el) for el in container.findall("SOURCE")]
    targets = [_parse_target(el) for el in container.findall("TARGET")]

    # Parse mapplet definitions for resolution during mapping parsing
    mapplet_defs: Dict[str, ET.Element] = {}
    for mplt_el in container.findall("MAPPLET"):
        mplt_name = mplt_el.get("NAME", "")
        if mplt_name:
            mapplet_defs[mplt_name] = mplt_el

    mappings = [_parse_mapping(el, sources, mapplet_defs) for el in container.findall("MAPPING")]

    # Parse worklets (sub-workflows) first so they can be referenced by workflows
    worklet_map: Dict[str, "Workflow"] = {}
    for wl_el in container.findall("WORKLET"):
        wl = _parse_workflow(wl_el)
        worklet_map[wl.name] = wl

    workflows = [_parse_workflow(el, worklet_map) for el in container.findall("WORKFLOW")]

    return Repository(
        name=repo_el.get("NAME", ""),
        folder=folder_name,
        connections=_extract_connections(container),
        sources=sources,
        targets=targets,
        mappings=mappings,
        workflows=workflows,
    )


def _parse_source(el: ET.Element) -> Source:
    columns = [
        Column(
            name=f.get("NAME", ""),
            datatype=f.get("DATATYPE", ""),
            precision=_int_or_none(f.get("PRECISION")),
            scale=_int_or_none(f.get("SCALE")),
            nullable=f.get("NULLABLE", "NULL") != "NOTNULL",
            description=f.get("DESCRIPTION") or None,
        )
        for f in el.findall("SOURCEFIELD")
    ]
    return Source(
        id=el.get("NAME", ""),
        name=el.get("NAME", ""),
        type="relational" if el.get("DATABASETYPE") else "flat_file",
        connection=el.get("DBDNAME") or None,
        schema_name=el.get("OWNERNAME") or None,
        table=el.get("NAME"),
        columns=columns,
    )


def _parse_target(el: ET.Element) -> Target:
    columns = [
        Column(
            name=f.get("NAME", ""),
            datatype=f.get("DATATYPE", ""),
            precision=_int_or_none(f.get("PRECISION")),
            scale=_int_or_none(f.get("SCALE")),
            nullable=f.get("NULLABLE", "NULL") != "NOTNULL",
            description=f.get("DESCRIPTION") or None,
        )
        for f in el.findall("TARGETFIELD")
    ]
    key_cols = [
        f.get("NAME", "")
        for f in el.findall("TARGETFIELD")
        if f.get("KEYTYPE") == "PRIMARY KEY"
    ]
    return Target(
        id=el.get("NAME", ""),
        name=el.get("NAME", ""),
        type="relational" if el.get("DATABASETYPE") else "flat_file",
        table=el.get("TABLENAME") or el.get("NAME"),
        columns=columns,
        key_columns=key_cols,
    )


def _parse_mapping(el: ET.Element, repo_sources: List[Source], mapplet_defs: Optional[Dict[str, ET.Element]] = None) -> Mapping:
    transforms = [_parse_transform(t) for t in el.findall("TRANSFORMATION")]

    # Resolve mapplet instances: inline their transforms into this mapping
    mapplet_instances: List[str] = []
    if mapplet_defs:
        transforms = _resolve_mapplets(el, transforms, mapplet_defs, mapplet_instances)

    connectors = _parse_connectors(el)

    # Wire up inputs/outputs from CONNECTOR elements
    for t in transforms:
        t.inputs = connectors.get(("to", t.name), [])
        t.outputs = connectors.get(("from", t.name), [])

    # Infer sequential DAG when no CONNECTORs exist for non-source transforms
    _infer_missing_inputs(transforms)

    # Build lookup of repo sources by name for cross-referencing
    repo_source_map: Dict[str, Source] = {s.name: s for s in repo_sources}

    # Extract source/target references from connectors
    sources: List[Source] = []
    targets: List[Target] = []
    for t in transforms:
        if t.type == TransformType.SOURCE_QUALIFIER:
            sq_name = t.name
            if sq_name.startswith("SQ_"):
                source_name = sq_name[3:]
                # Cross-reference with repo-level source for connection/schema
                repo_src = repo_source_map.get(source_name)
                if repo_src:
                    sources.append(repo_src)
                else:
                    sources.append(Source(
                        id=source_name,
                        name=source_name,
                        type="relational",
                        table=source_name,
                    ))
        elif t.type == TransformType.TARGET:
            targets.append(Target(
                id=t.name,
                name=t.name,
                type="relational",
                table=t.name,
            ))

    # Also detect targets from TARGETLOADORDER elements
    for tlo in el.findall("TARGETLOADORDER"):
        tgt_name = tlo.get("TARGETINSTANCE", "")
        if tgt_name and not any(t.name == tgt_name for t in targets):
            targets.append(Target(
                id=tgt_name,
                name=tgt_name,
                type="relational",
                table=tgt_name,
            ))

    return Mapping(
        id=el.get("NAME", ""),
        name=el.get("NAME", ""),
        description=el.get("DESCRIPTION") or None,
        sources=sources,
        targets=targets,
        transforms=transforms,
    )


def _parse_transform(el: ET.Element) -> Transform:
    type_str = el.get("TYPE", "")
    transform_type = _TRANSFORM_TYPE_MAP.get(type_str, TransformType.CUSTOM)
    name = el.get("NAME", "")

    # Handle Custom Transformations that map to standard types (e.g., Union)
    if transform_type == TransformType.CUSTOM:
        template = el.get("TEMPLATENAME", "")
        if template == "Union Transformation":
            transform_type = TransformType.UNION

    expressions: List[FieldExpression] = []
    output_ports: List[str] = []
    for field in el.findall("TRANSFORMFIELD"):
        expr_text = field.get("EXPRESSION", "")
        port_type = field.get("PORTTYPE", "")
        field_name = field.get("NAME", "")

        if "OUTPUT" in port_type:
            output_ports.append(field_name)
            # Only capture meaningful expressions (non-passthrough logic)
            if expr_text and expr_text != field_name:
                expressions.append(
                    FieldExpression(
                        output_field=field_name,
                        expression=expr_text,
                        datatype=field.get("DATATYPE"),
                        description=field.get("DESCRIPTION") or None,
                    )
                )

    # Extract properties from TABLEATTRIBUTE elements
    properties: Dict[str, str] = {}
    filter_condition: Optional[str] = None
    lookup_config: Optional[LookupConfig] = None

    for attr in el.findall("TABLEATTRIBUTE"):
        attr_name = attr.get("NAME", "")
        attr_value = attr.get("VALUE", "")
        if attr_value:
            properties[attr_name] = attr_value

    # Extract group-by ports from TRANSFORMFIELD GROUP="YES" attribute (Aggregator)
    if transform_type == TransformType.AGGREGATOR and "Group By Ports" not in properties:
        group_fields = [
            f.get("NAME", "")
            for f in el.findall("TRANSFORMFIELD")
            if f.get("GROUP", "").upper() == "YES"
        ]
        if group_fields:
            properties["Group By Ports"] = ", ".join(group_fields)

    if transform_type == TransformType.FILTER:
        filter_condition = properties.get("Filter Condition")

    # Router groups: each group has a name and filter condition
    router_groups: List[RouterGroup] = []
    if transform_type == TransformType.ROUTER:
        # Router groups are stored as GROUP child elements with NAME and EXPRESSION attributes
        for group_el in el.findall("GROUP"):
            grp_name = group_el.get("NAME", "")
            grp_expr = group_el.get("EXPRESSION", "")
            if grp_name and grp_expr:
                router_groups.append(RouterGroup(name=grp_name, condition=grp_expr))
        # Fallback: check TABLEATTRIBUTE pattern
        if not router_groups:
            for key, value in properties.items():
                if "filter" in key.lower() and value:
                    group_name = key.replace(" Filter", "").replace(" Condition", "").strip()
                    router_groups.append(RouterGroup(name=group_name, condition=value))

    # SCD config
    scd_config = None
    if transform_type == TransformType.SCD:
        scd_type_str = properties.get("SCD Type", "Type 2")
        scd_type = SCDType.TYPE_2
        if "1" in scd_type_str:
            scd_type = SCDType.TYPE_1
        elif "3" in scd_type_str:
            scd_type = SCDType.TYPE_3

        key_cols_str = properties.get("Key Columns", "")
        tracked_cols_str = properties.get("Tracked Columns", "")

        scd_config = SCDConfig(
            scd_type=scd_type,
            key_columns=[c.strip() for c in key_cols_str.split(",") if c.strip()],
            tracked_columns=[c.strip() for c in tracked_cols_str.split(",") if c.strip()],
            effective_date_field=properties.get("Effective Date Field"),
            end_date_field=properties.get("End Date Field"),
            current_flag_field=properties.get("Current Flag Field"),
        )

    if transform_type == TransformType.LOOKUP:
        # Extract default values from fields
        defaults: Dict[str, str] = {}
        return_fields: List[str] = []
        for field in el.findall("TRANSFORMFIELD"):
            if "OUTPUT" in field.get("PORTTYPE", ""):
                fname = field.get("NAME", "")
                return_fields.append(fname)
                default = field.get("DEFAULTVALUE", "")
                if default:
                    defaults[fname] = default

        lookup_source = properties.get("Lookup table name", "")
        if not lookup_source:
            # Extract table from SQL override: "SELECT ... FROM <table> ..."
            sql_override = properties.get("Lookup Sql Override", "")
            if sql_override:
                lookup_source = _extract_table_from_sql(sql_override)
        if not lookup_source:
            # Derive from transform name: lkp_CUSTOMER_DIM → customer_dim
            derived = name
            for prefix in ("lkp_", "LKP_", "Lkp_"):
                if derived.startswith(prefix):
                    derived = derived[len(prefix):]
                    break
            lookup_source = derived.lower()

        lookup_config = LookupConfig(
            lookup_source=lookup_source,
            lookup_condition=properties.get("Lookup condition", ""),
            return_fields=return_fields,
            default_on_miss=defaults,
        )

    return Transform(
        id=name,
        name=name,
        type=transform_type,
        expressions=expressions,
        output_ports=output_ports,
        filter_condition=filter_condition,
        lookup_config=lookup_config,
        router_groups=router_groups,
        scd_config=scd_config,
        properties=properties,
    )


def _parse_connectors(mapping_el: ET.Element) -> Dict[Tuple[str, str], List[str]]:
    """Parse CONNECTOR elements to build input/output relationships.

    Returns a dict keyed by ("to", instance_name) -> list of source instance names
    and ("from", instance_name) -> list of target instance names.
    """
    to_map: Dict[str, Set[str]] = {}
    from_map: Dict[str, Set[str]] = {}

    for conn in mapping_el.findall("CONNECTOR"):
        from_inst = conn.get("FROMINSTANCE", "")
        to_inst = conn.get("TOINSTANCE", "")

        to_map.setdefault(to_inst, set()).add(from_inst)
        from_map.setdefault(from_inst, set()).add(to_inst)

    result: Dict[Tuple[str, str], List[str]] = {}
    for inst, sources in to_map.items():
        result[("to", inst)] = sorted(sources)
    for inst, targets in from_map.items():
        result[("from", inst)] = sorted(targets)
    return result


def _parse_workflow(el: ET.Element, worklet_map: Optional[Dict[str, "Workflow"]] = None) -> Workflow:
    name = el.get("NAME", "")

    # Parse schedule
    schedule_cron: Optional[str] = None
    sched_el = el.find("SCHEDULER")
    if sched_el is not None:
        info = sched_el.find("SCHEDULEINFO")
        if info is not None:
            # Format 1: REPEATINTERVAL attribute (older/alternate format)
            interval = _int_or_none(info.get("REPEATINTERVAL"))
            start = info.get("STARTDATE", "")
            if interval:
                schedule_cron = _interval_to_cron(interval, start)
            else:
                # Format 2: DTD-compliant nested elements
                start_opts = info.find("STARTOPTIONS")
                sched_opts = info.find("SCHEDULEOPTIONS")
                hour, minute = 0, 0
                if start_opts is not None:
                    time_str = start_opts.get("STARTTIME", "00:00:00")
                    parts = time_str.split(":")
                    hour = int(parts[0]) if parts[0].isdigit() else 0
                    minute = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

                if sched_opts is not None:
                    recurring = sched_opts.find("RECURRING")
                    if recurring is not None:
                        days = _int_or_none(recurring.get("DAYS")) or 0
                        hours = _int_or_none(recurring.get("HOURS")) or 0
                        if days == 1 and hours == 0:
                            schedule_cron = f"{minute} {hour} * * *"
                        elif days == 7:
                            schedule_cron = f"{minute} {hour} * * 0"
                        elif hours > 0:
                            schedule_cron = f"{minute} */{hours} * * *"
                        else:
                            schedule_cron = f"{minute} {hour} * * *"
                    elif sched_opts.get("SCHEDULETYPE") == "ONCE":
                        schedule_cron = f"{minute} {hour} * * * # one-time"
                else:
                    schedule_cron = f"{minute} {hour} * * *"

    # Parse tasks (sessions and other task types)
    tasks: List[WorkflowTask] = []
    for task_el in el.findall("TASK"):
        tasks.append(
            WorkflowTask(
                id=task_el.get("NAME", ""),
                name=task_el.get("NAME", ""),
                type=task_el.get("TYPE", "").lower(),
            )
        )

    for session_el in el.findall("SESSION"):
        session_config = SessionConfig(
            mapping_name=session_el.get("MAPPINGNAME", ""),
            properties={
                a.get("NAME", ""): a.get("VALUE", "")
                for a in session_el.findall("ATTRIBUTE")
            },
        )
        # Extract connection overrides
        for ext in session_el.findall("SESSIONEXTENSION"):
            for conn_ref in ext.findall("CONNECTIONREFERENCE"):
                conn_name = conn_ref.get("CONNECTIONNAME", "")
                inst_name = ext.get("SINSTANCENAME", "")
                if conn_name:
                    session_config.connection_overrides[inst_name] = conn_name

        tasks.append(
            WorkflowTask(
                id=session_el.get("NAME", ""),
                name=session_el.get("NAME", ""),
                type="session",
                session_config=session_config,
            )
        )

    # Parse WORKLETINST references (worklet instances in this workflow)
    for wl_inst in el.findall("WORKLETINST"):
        wl_name = wl_inst.get("WORKLETNAME", "") or wl_inst.get("NAME", "")
        inst_name = wl_inst.get("NAME", wl_name)
        tasks.append(
            WorkflowTask(
                id=inst_name,
                name=inst_name,
                type="worklet",
            )
        )

    # Parse workflow links
    links: List[WorkflowLink] = []
    for link_el in el.findall("WORKFLOWLINK"):
        condition = link_el.get("CONDITION", "")
        link_type = WorkflowLinkType.SUCCESS
        if condition:
            link_type = WorkflowLinkType.CONDITIONAL

        links.append(
            WorkflowLink(
                from_task=link_el.get("FROMTASK", ""),
                to_task=link_el.get("TOTASK", ""),
                link_type=link_type,
                condition=condition or None,
            )
        )

    return Workflow(
        id=name,
        name=name,
        tasks=tasks,
        links=links,
        schedule_cron=schedule_cron,
        worklets=worklet_map or {},
    )


def _resolve_mapplets(
    mapping_el: ET.Element,
    transforms: List[Transform],
    mapplet_defs: Dict[str, ET.Element],
    mapplet_instances: List[str],
) -> List[Transform]:
    """Inline mapplet transforms into the mapping, rewiring connectors.

    For each MAPPLET INSTANCE in the mapping:
    1. Find the mapplet definition
    2. Parse its internal transforms (skip Input/Output Transformations)
    3. Insert them into the transform list
    4. Rewrite CONNECTOR elements so that connections to/from the mapplet
       instance point to the first/last internal transform instead.
    """
    # Find INSTANCE elements with TYPE="MAPPLET"
    mplt_insts = [
        inst for inst in mapping_el.findall("INSTANCE")
        if inst.get("TYPE", "").upper() == "MAPPLET"
    ]
    if not mplt_insts:
        return transforms

    for inst in mplt_insts:
        inst_name = inst.get("NAME", "")
        mplt_name = inst.get("TRANSFORMATION_NAME", "") or inst_name
        mapplet_instances.append(inst_name)

        mplt_el = mapplet_defs.get(mplt_name)
        if mplt_el is None:
            continue

        # Parse the mapplet's internal transforms
        mplt_transforms = [_parse_transform(t) for t in mplt_el.findall("TRANSFORMATION")]
        mplt_connectors = _parse_connectors(mplt_el)

        # Wire internal transforms
        for t in mplt_transforms:
            t.inputs = mplt_connectors.get(("to", t.name), [])
            t.outputs = mplt_connectors.get(("from", t.name), [])

        # Identify input/output boundary transforms
        input_transform = None
        output_transform = None
        internal_transforms: List[Transform] = []
        for t_el in mplt_el.findall("TRANSFORMATION"):
            t_type = t_el.get("TYPE", "")
            t_name = t_el.get("NAME", "")
            if t_type == "Input Transformation":
                input_transform = next((t for t in mplt_transforms if t.name == t_name), None)
            elif t_type == "Output Transformation":
                output_transform = next((t for t in mplt_transforms if t.name == t_name), None)
            else:
                match = next((t for t in mplt_transforms if t.name == t_name), None)
                if match:
                    internal_transforms.append(match)

        if not internal_transforms:
            continue

        # Rewire: replace references to the mapplet instance in mapping CONNECTORs
        # Connections TO mapplet instance → connect to first internal transform
        # Connections FROM mapplet instance → connect from last internal transform
        first_internal = internal_transforms[0].name
        last_internal = internal_transforms[-1].name

        # Find the last internal transform that has output ports (for FROM rewiring)
        if output_transform and output_transform.inputs:
            # The output transform's inputs tell us which internal transforms feed it
            last_internal = output_transform.inputs[0]

        # Rewrite CONNECTOR elements in the mapping XML
        for conn in mapping_el.findall("CONNECTOR"):
            if conn.get("TOINSTANCE") == inst_name:
                conn.set("TOINSTANCE", first_internal)
                conn.set("TOINSTANCETYPE", "Expression")
            if conn.get("FROMINSTANCE") == inst_name:
                conn.set("FROMINSTANCE", last_internal)
                conn.set("FROMINSTANCETYPE", "Expression")

        # Insert internal transforms into the mapping's transform list
        # Place them after the source qualifier (or at position 1)
        insert_idx = 1
        for i, t in enumerate(transforms):
            if t.type == TransformType.SOURCE_QUALIFIER:
                insert_idx = i + 1
                break
        for j, t in enumerate(internal_transforms):
            transforms.insert(insert_idx + j, t)

    return transforms


def _infer_missing_inputs(transforms: List[Transform]) -> None:
    """Infer inputs for transforms that have no CONNECTOR wiring.

    Sequence Generators are standalone (no inputs needed).
    For other transforms without inputs, link to the previous transform in order.
    """
    prev_name: Optional[str] = None
    for t in transforms:
        if t.type == TransformType.SEQUENCE_GENERATOR:
            # Sequence generators are self-contained; no input needed
            continue
        if t.type == TransformType.SOURCE_QUALIFIER:
            prev_name = t.name
            continue
        if not t.inputs and prev_name:
            t.inputs = [prev_name]
        if t.name:
            prev_name = t.name


def _extract_connections(container: ET.Element) -> List[Connection]:
    """Extract connection info from SESSION elements within workflows."""
    connections: Dict[str, Connection] = {}
    for wf in container.findall("WORKFLOW"):
        for session in wf.findall("SESSION"):
            for ext in session.findall("SESSIONEXTENSION"):
                for conn_ref in ext.findall("CONNECTIONREFERENCE"):
                    conn_name = conn_ref.get("CONNECTIONNAME", "")
                    conn_type = conn_ref.get("CONNECTIONTYPE", "")
                    if conn_name and conn_name not in connections:
                        connections[conn_name] = Connection(
                            name=conn_name,
                            type=conn_type.lower() if conn_type else "relational",
                        )
    return list(connections.values())


def _interval_to_cron(interval: Optional[int], start: str) -> Optional[str]:
    """Convert Informatica schedule interval + start time to cron expression."""
    if not interval:
        return None

    # Extract hour and minute from start date string (format: "MM/DD/YYYY HH:MM:SS")
    hour, minute = 0, 0
    if " " in start:
        time_part = start.split(" ", 1)[1]
        time_parts = time_part.split(":")
        if len(time_parts) >= 2:
            hour = int(time_parts[0]) if time_parts[0].isdigit() else 0
            minute = int(time_parts[1]) if time_parts[1].isdigit() else 0

    if interval == 86400:  # daily
        return f"{minute} {hour} * * *"
    elif interval == 3600:  # hourly
        return f"{minute} * * * *"
    elif interval == 604800:  # weekly
        return f"{minute} {hour} * * 0"
    else:
        # For non-standard intervals, store as metadata
        return f"{minute} {hour} * * * # every {interval}s"


def _int_or_none(val: Optional[str]) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


import re as _re

def _extract_table_from_sql(sql: str) -> str:
    """Extract the first table name from a SQL SELECT statement."""
    match = _re.search(r'\bFROM\s+(\S+)', sql, _re.IGNORECASE)
    return match.group(1) if match else ""
