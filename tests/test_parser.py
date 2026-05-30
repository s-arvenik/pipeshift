"""Tests for the Informatica PowerCenter XML parser."""

from pathlib import Path

from pipeshift.ir.schema import TransformType
from pipeshift.parser.informatica_xml import parse_file

SAMPLE_DIR = Path(__file__).parent / "sample_exports"


class TestParseCustomerDim:
    """Test parsing the customer_dim.xml sample export."""

    def setup_method(self):
        self.repo = parse_file(SAMPLE_DIR / "customer_dim.xml")

    def test_repository_metadata(self):
        assert self.repo.name == "DEV_REPO"
        assert self.repo.folder == "SALES_DW"

    def test_sources_parsed(self):
        assert len(self.repo.sources) == 2
        names = {s.name for s in self.repo.sources}
        assert names == {"CUSTOMERS", "REGION_MAPPING"}

    def test_source_columns(self):
        customers = next(s for s in self.repo.sources if s.name == "CUSTOMERS")
        assert len(customers.columns) == 7
        assert customers.columns[0].name == "CUSTOMER_ID"
        assert customers.columns[0].datatype == "number"
        assert customers.columns[0].nullable is False  # NOTNULL
        assert customers.schema_name == "CRM"

    def test_targets_parsed(self):
        assert len(self.repo.targets) == 1
        target = self.repo.targets[0]
        assert target.name == "DIM_CUSTOMER"
        assert len(target.columns) == 8
        assert target.key_columns == ["CUSTOMER_KEY"]

    def test_mapping_parsed(self):
        assert len(self.repo.mappings) == 1
        mapping = self.repo.mappings[0]
        assert mapping.name == "m_customer_dim"
        assert mapping.description == "Load customer dimension from CRM source"

    def test_transforms_parsed(self):
        mapping = self.repo.mappings[0]
        assert len(mapping.transforms) == 4
        types = {t.type for t in mapping.transforms}
        assert types == {
            TransformType.SOURCE_QUALIFIER,
            TransformType.EXPRESSION,
            TransformType.FILTER,
            TransformType.LOOKUP,
        }

    def test_expression_transform(self):
        mapping = self.repo.mappings[0]
        exp = next(t for t in mapping.transforms if t.type == TransformType.EXPRESSION)
        assert exp.name == "EXP_DERIVE_FIELDS"
        # Should have 3 output expressions (FULL_NAME, CUSTOMER_STATUS, LOAD_DATE)
        assert len(exp.expressions) == 3
        full_name_expr = next(e for e in exp.expressions if e.output_field == "FULL_NAME")
        assert "LTRIM" in full_name_expr.expression
        assert "RTRIM" in full_name_expr.expression

    def test_filter_transform(self):
        mapping = self.repo.mappings[0]
        fil = next(t for t in mapping.transforms if t.type == TransformType.FILTER)
        assert fil.name == "FIL_ACTIVE_ONLY"
        assert fil.filter_condition == "CUSTOMER_STATUS != 'INACTIVE'"

    def test_lookup_transform(self):
        mapping = self.repo.mappings[0]
        lkp = next(t for t in mapping.transforms if t.type == TransformType.LOOKUP)
        assert lkp.name == "LKP_REGION"
        assert lkp.lookup_config is not None
        assert lkp.lookup_config.lookup_source == "REF.REGION_MAPPING"
        assert lkp.lookup_config.lookup_condition == "ZIP_CODE = IN_ZIP_CODE"
        assert "REGION_NAME" in lkp.lookup_config.return_fields
        assert lkp.lookup_config.default_on_miss["REGION_NAME"] == "UNKNOWN"

    def test_connector_wiring(self):
        """CONNECTOR elements should wire up inputs/outputs between transforms."""
        mapping = self.repo.mappings[0]
        exp = next(t for t in mapping.transforms if t.name == "EXP_DERIVE_FIELDS")
        # EXP_DERIVE_FIELDS receives from SQ_CUSTOMERS
        assert "SQ_CUSTOMERS" in exp.inputs
        # EXP_DERIVE_FIELDS sends to FIL_ACTIVE_ONLY
        assert "FIL_ACTIVE_ONLY" in exp.outputs

    def test_workflow_parsed(self):
        assert len(self.repo.workflows) == 1
        wf = self.repo.workflows[0]
        assert wf.name == "wf_customer_dim_daily"
        assert wf.schedule_cron == "0 2 * * *"

    def test_workflow_tasks(self):
        wf = self.repo.workflows[0]
        assert len(wf.tasks) == 2  # Start + session
        session = next(t for t in wf.tasks if t.type == "session")
        assert session.name == "s_m_customer_dim"
        assert session.session_config is not None
        assert session.session_config.mapping_name == "m_customer_dim"

    def test_workflow_links(self):
        wf = self.repo.workflows[0]
        assert len(wf.links) == 1
        link = wf.links[0]
        assert link.from_task == "Start"
        assert link.to_task == "s_m_customer_dim"

    def test_connections_extracted(self):
        assert len(self.repo.connections) == 2
        conn_names = {c.name for c in self.repo.connections}
        assert "DW_ORACLE_CONN" in conn_names
        assert "CRM_ORACLE_CONN" in conn_names

    def test_session_connection_overrides(self):
        wf = self.repo.workflows[0]
        session = next(t for t in wf.tasks if t.type == "session")
        overrides = session.session_config.connection_overrides
        assert overrides["DIM_CUSTOMER"] == "DW_ORACLE_CONN"
        assert overrides["SQ_CUSTOMERS"] == "CRM_ORACLE_CONN"
