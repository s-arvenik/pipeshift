"""Integration tests: full pipeline against enterprise PowerCenter fixtures."""

import tempfile
from pathlib import Path

import pytest

from pipeshift.parser.informatica_xml import parse_file
from pipeshift.generator import generate_dbt_project
from pipeshift.orchestration import generate_step_functions

SAMPLES = Path(__file__).parent / "sample_exports"
MULTI = SAMPLES / "powercenter_multi_mapping_export.xml"
EDGE = SAMPLES / "powercenter_enterprise_edge_cases.xml"


# --- Parser integration ---


class TestMultiMappingParsing:
    @pytest.fixture(autouse=True)
    def repo(self):
        self.repo = parse_file(MULTI)

    def test_parses_all_mappings(self):
        assert len(self.repo.mappings) == 6

    def test_mapping_names(self):
        names = {m.name for m in self.repo.mappings}
        assert "m_LOAD_CUSTOMER_DIM" in names
        assert "m_LOAD_ORDER_FACT" in names
        assert "m_LOAD_PRODUCT_DIM_WITH_MAPPLET" in names
        assert "m_LOAD_DAILY_SALES_SUMMARY" in names
        assert "m_LOAD_STATUS_REFERENCE" in names

    def test_workflow_parsed(self):
        assert len(self.repo.workflows) == 1
        assert self.repo.workflows[0].name == "wf_DAILY_WAREHOUSE_LOAD"

    def test_sources_extracted(self):
        assert len(self.repo.sources) >= 4

    def test_lookup_transforms_recognized(self):
        customer_dim = next(m for m in self.repo.mappings if m.name == "m_LOAD_CUSTOMER_DIM")
        lookup_types = [t for t in customer_dim.transforms if t.type.value == "lookup"]
        assert len(lookup_types) >= 2

    def test_sequence_transforms_recognized(self):
        customer_dim = next(m for m in self.repo.mappings if m.name == "m_LOAD_CUSTOMER_DIM")
        seq_types = [t for t in customer_dim.transforms if t.type.value == "sequence_generator"]
        assert len(seq_types) == 1

    def test_router_groups_parsed(self):
        customer_dim = next(m for m in self.repo.mappings if m.name == "m_LOAD_CUSTOMER_DIM")
        router = next(t for t in customer_dim.transforms if t.type.value == "router")
        assert len(router.router_groups) == 2
        assert router.router_groups[0].name == "INSERT_ROWS"

    def test_aggregator_group_by_extracted(self):
        order_fact = next(m for m in self.repo.mappings if m.name == "m_LOAD_ORDER_FACT")
        agg = next(t for t in order_fact.transforms if t.type.value == "aggregator")
        assert "Group By Ports" in agg.properties

    def test_dag_inferred_when_no_connectors(self):
        order_fact = next(m for m in self.repo.mappings if m.name == "m_LOAD_ORDER_FACT")
        # Transforms after SQ should have inputs inferred
        non_sq = [t for t in order_fact.transforms if t.type.value != "source_qualifier" and t.type.value != "sequence_generator"]
        for t in non_sq:
            assert t.inputs, f"{t.name} has no inputs"


class TestEdgeCasesParsing:
    @pytest.fixture(autouse=True)
    def repo(self):
        self.repo = parse_file(EDGE)

    def test_parses_all_mappings(self):
        assert len(self.repo.mappings) == 8

    def test_workflow_parsed(self):
        assert len(self.repo.workflows) == 1
        wf = self.repo.workflows[0]
        assert wf.name == "wf_ENTERPRISE_DAILY_LOAD"

    def test_worklet_parsed(self):
        wf = self.repo.workflows[0]
        assert "wl_LOAD_CORE_DIMS" in wf.worklets

    def test_worklet_has_sessions(self):
        wf = self.repo.workflows[0]
        wl = wf.worklets["wl_LOAD_CORE_DIMS"]
        session_names = [t.name for t in wl.tasks if t.type == "session"]
        assert "s_ACCOUNT_DIM_SCD2_FULL" in session_names
        assert "s_HIERARCHY_BRIDGE_SELF_JOIN" in session_names

    def test_router_groups_in_edge_cases(self):
        acct = next(m for m in self.repo.mappings if m.name == "m_ACCOUNT_DIM_SCD2_FULL")
        router = next(t for t in acct.transforms if t.type.value == "router")
        assert len(router.router_groups) == 3


# --- Generator integration ---


class TestMultiMappingGeneration:
    @pytest.fixture(autouse=True)
    def generated(self, tmp_path):
        repo = parse_file(MULTI)
        self.files = generate_dbt_project(repo, tmp_path)
        self.output = tmp_path

    def test_generates_all_models(self):
        models = list((self.output / "models").glob("m_*.sql"))
        assert len(models) == 6

    def test_sources_yml_created(self):
        assert (self.output / "models" / "_sources.yml").exists()

    def test_orchestration_created(self):
        repo = parse_file(MULTI)
        assert len(repo.workflows) == 1
        asl = generate_step_functions(repo.workflows[0])
        assert "States" in asl
        assert len(asl["States"]) >= 6

    def test_no_from_unknown_in_connected_mappings(self):
        # m_LOAD_CUSTOMER_DIM has CONNECTORs — should have no FROM unknown
        sql = (self.output / "models" / "m_load_customer_dim.sql").read_text()
        assert "FROM unknown" not in sql

    def test_router_produces_union_all(self):
        sql = (self.output / "models" / "m_load_customer_dim.sql").read_text()
        assert "UNION ALL" in sql
        assert "route_insert_rows" in sql

    def test_lookup_has_join(self):
        sql = (self.output / "models" / "m_load_customer_dim.sql").read_text()
        assert "LEFT JOIN" in sql

    def test_sequence_generator_standalone(self):
        sql = (self.output / "models" / "m_load_customer_dim.sql").read_text()
        assert "ROW_NUMBER() OVER(ORDER BY 1)" in sql
        assert "(SELECT 1) AS _seq" in sql

    def test_order_fact_dag_resolved(self):
        sql = (self.output / "models" / "m_load_order_fact.sql").read_text()
        assert "FROM unknown" not in sql

    def test_aggregator_has_group_by(self):
        sql = (self.output / "models" / "m_load_order_fact.sql").read_text()
        assert "GROUP BY" in sql


class TestEdgeCasesGeneration:
    @pytest.fixture(autouse=True)
    def generated(self, tmp_path):
        repo = parse_file(EDGE)
        self.files = generate_dbt_project(repo, tmp_path)
        self.output = tmp_path

    def test_generates_all_models(self):
        models = list((self.output / "models").glob("m_*.sql"))
        assert len(models) == 8

    def test_no_crash(self):
        # The fact we got here means no IndexError
        assert (self.output / "models" / "_sources.yml").exists()

    def test_worklet_expanded_in_orchestration(self):
        repo = parse_file(EDGE)
        asl = generate_step_functions(repo.workflows[0])
        states = asl["States"]
        # Worklet sessions should be inlined
        assert "s_ACCOUNT_DIM_SCD2_FULL" in states
        assert "s_HIERARCHY_BRIDGE_SELF_JOIN" in states

    def test_scd2_mapping_has_router_groups(self):
        sql = (self.output / "models" / "m_account_dim_scd2_full.sql").read_text()
        assert "route_new_account" in sql
        assert "route_changed_account" in sql

    def test_lookup_condition_with_literal(self):
        sql = (self.output / "models" / "m_account_dim_scd2_full.sql").read_text()
        assert "'Y'" in sql
