"""Pydantic models for the PipeShift Intermediate Representation (IR).

This schema captures the full structure of an ETL pipeline in a source-agnostic way:
- Sources and targets (relational, flat file, XML)
- Transformations (expression, filter, joiner, lookup, aggregator, etc.)
- Expressions (individual field-level calculations)
- Workflows (orchestration: sessions, links, conditions, scheduling)
- Lineage (column-level data flow)
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --- Enums ---


class TransformType(str, Enum):
    SOURCE_QUALIFIER = "source_qualifier"
    EXPRESSION = "expression"
    FILTER = "filter"
    JOINER = "joiner"
    LOOKUP = "lookup"
    AGGREGATOR = "aggregator"
    ROUTER = "router"
    UNION = "union"
    SORTER = "sorter"
    RANK = "rank"
    NORMALIZER = "normalizer"
    SEQUENCE_GENERATOR = "sequence_generator"
    SCD = "slowly_changing_dimension"
    UPDATE_STRATEGY = "update_strategy"
    CUSTOM = "custom"
    JAVA = "java"
    STORED_PROCEDURE = "stored_procedure"
    TARGET = "target"


class JoinType(str, Enum):
    INNER = "inner"
    LEFT_OUTER = "left_outer"
    RIGHT_OUTER = "right_outer"
    FULL_OUTER = "full_outer"


class SCDType(str, Enum):
    TYPE_1 = "type_1"
    TYPE_2 = "type_2"
    TYPE_3 = "type_3"


class LoadType(str, Enum):
    INSERT = "insert"
    UPDATE = "update"
    UPSERT = "upsert"
    DELETE = "delete"


class Complexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    MANUAL = "manual"


class WorkflowLinkType(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    CONDITIONAL = "conditional"


# --- Column & Connection ---


class Column(BaseModel):
    name: str
    datatype: str
    precision: Optional[int] = None
    scale: Optional[int] = None
    nullable: bool = True
    description: Optional[str] = None


class Connection(BaseModel):
    name: str
    type: str
    database: Optional[str] = None
    schema_name: Optional[str] = None
    properties: Dict[str, str] = Field(default_factory=dict)


# --- Sources & Targets ---


class Source(BaseModel):
    id: str
    name: str
    type: str
    connection: Optional[str] = None
    schema_name: Optional[str] = None
    table: Optional[str] = None
    columns: List[Column] = Field(default_factory=list)


class Target(BaseModel):
    id: str
    name: str
    type: str
    connection: Optional[str] = None
    schema_name: Optional[str] = None
    table: Optional[str] = None
    columns: List[Column] = Field(default_factory=list)
    load_type: LoadType = LoadType.INSERT
    key_columns: List[str] = Field(default_factory=list)


# --- Expressions & Transforms ---


class FieldExpression(BaseModel):
    """A single field-level expression within a transformation."""

    output_field: str
    expression: str
    datatype: Optional[str] = None
    description: Optional[str] = None


class LookupConfig(BaseModel):
    lookup_source: str
    lookup_condition: str
    return_fields: List[str] = Field(default_factory=list)
    default_on_miss: Dict[str, Any] = Field(default_factory=dict)


class JoinConfig(BaseModel):
    join_type: JoinType = JoinType.INNER
    join_condition: str = ""


class RouterGroup(BaseModel):
    name: str
    condition: str


class SCDConfig(BaseModel):
    scd_type: SCDType = SCDType.TYPE_2
    key_columns: List[str] = Field(default_factory=list)
    tracked_columns: List[str] = Field(default_factory=list)
    effective_date_field: Optional[str] = None
    end_date_field: Optional[str] = None
    current_flag_field: Optional[str] = None


class Transform(BaseModel):
    """A single transformation node in the mapping DAG."""

    id: str
    name: str
    type: TransformType
    inputs: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    output_ports: List[str] = Field(default_factory=list)
    expressions: List[FieldExpression] = Field(default_factory=list)
    filter_condition: Optional[str] = None
    join_config: Optional[JoinConfig] = None
    lookup_config: Optional[LookupConfig] = None
    router_groups: List[RouterGroup] = Field(default_factory=list)
    scd_config: Optional[SCDConfig] = None
    properties: Dict[str, str] = Field(default_factory=dict)


# --- Mapping ---


class Mapping(BaseModel):
    """A complete ETL mapping (transformation pipeline)."""

    id: str
    name: str
    description: Optional[str] = None
    sources: List[Source] = Field(default_factory=list)
    targets: List[Target] = Field(default_factory=list)
    transforms: List[Transform] = Field(default_factory=list)
    mapplet_instances: List[str] = Field(default_factory=list)
    parameters: Dict[str, str] = Field(default_factory=dict)
    complexity: Optional[Complexity] = None
    complexity_score: Optional[float] = None


# --- Workflow & Orchestration ---


class SessionConfig(BaseModel):
    mapping_name: str
    connection_overrides: Dict[str, str] = Field(default_factory=dict)
    commit_interval: Optional[int] = None
    properties: Dict[str, str] = Field(default_factory=dict)


class WorkflowLink(BaseModel):
    from_task: str
    to_task: str
    link_type: WorkflowLinkType = WorkflowLinkType.SUCCESS
    condition: Optional[str] = None


class WorkflowTask(BaseModel):
    id: str
    name: str
    type: str
    session_config: Optional[SessionConfig] = None
    command: Optional[str] = None
    properties: Dict[str, str] = Field(default_factory=dict)


class Workflow(BaseModel):
    """Orchestration definition: execution order, conditions, scheduling."""

    id: str
    name: str
    tasks: List[WorkflowTask] = Field(default_factory=list)
    links: List[WorkflowLink] = Field(default_factory=list)
    schedule_cron: Optional[str] = None
    parameters: Dict[str, str] = Field(default_factory=dict)
    worklets: Dict[str, "Workflow"] = Field(default_factory=dict)


# --- Top-level Repository ---


class Repository(BaseModel):
    """Top-level container representing an Informatica repository export."""

    name: str
    folder: Optional[str] = None
    connections: List[Connection] = Field(default_factory=list)
    sources: List[Source] = Field(default_factory=list)
    targets: List[Target] = Field(default_factory=list)
    mappings: List[Mapping] = Field(default_factory=list)
    workflows: List[Workflow] = Field(default_factory=list)
