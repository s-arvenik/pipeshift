"""Tests for Aggregator and Joiner transform support."""

import tempfile
from pathlib import Path

from pipeshift.generator import generate_dbt_project
from pipeshift.ir.schema import TransformType
from pipeshift.parser.informatica_xml import parse_file

SAMPLE_DIR = Path(__file__).parent / "sample_exports"


class TestAggregatorJoinerParsing:
    def setup_method(self):
        self.repo = parse_file(SAMPLE_DIR / "agg_orders.xml")

    def test_mapping_has_joiner(self):
        mapping = self.repo.mappings[0]
        joiners = [t for t in mapping.transforms if t.type == TransformType.JOINER]
        assert len(joiners) == 1
        assert joiners[0].name == "JNR_ORDERS_CUSTOMERS"

    def test_joiner_has_condition(self):
        mapping = self.repo.mappings[0]
        joiner = next(t for t in mapping.transforms if t.type == TransformType.JOINER)
        assert joiner.properties.get("Join Condition") == "CUSTOMER_ID = CUSTOMER_ID1"

    def test_mapping_has_aggregator(self):
        mapping = self.repo.mappings[0]
        aggs = [t for t in mapping.transforms if t.type == TransformType.AGGREGATOR]
        assert len(aggs) == 1
        assert aggs[0].name == "AGG_BY_CUSTOMER"

    def test_aggregator_expressions(self):
        mapping = self.repo.mappings[0]
        agg = next(t for t in mapping.transforms if t.type == TransformType.AGGREGATOR)
        assert len(agg.expressions) == 2
        expr_names = {e.output_field for e in agg.expressions}
        assert "TOTAL_AMOUNT" in expr_names
        assert "ORDER_COUNT" in expr_names

    def test_aggregator_group_by(self):
        mapping = self.repo.mappings[0]
        agg = next(t for t in mapping.transforms if t.type == TransformType.AGGREGATOR)
        assert "Group By Ports" in agg.properties
        assert "CUSTOMER_ID" in agg.properties["Group By Ports"]

    def test_joiner_inputs_wired(self):
        mapping = self.repo.mappings[0]
        joiner = next(t for t in mapping.transforms if t.type == TransformType.JOINER)
        assert "SQ_ORDERS" in joiner.inputs
        assert "SQ_CUSTOMERS" in joiner.inputs


class TestAggregatorJoinerGeneration:
    def setup_method(self):
        self.repo = parse_file(SAMPLE_DIR / "agg_orders.xml")
        self.tmp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.tmp_dir) / "dbt_output"
        generate_dbt_project(self.repo, self.output_dir)
        self.model_path = self.output_dir / "models" / "m_agg_customer_orders.sql"
        self.model_sql = self.model_path.read_text()

    def test_model_generated(self):
        assert self.model_path.exists()

    def test_has_join(self):
        # Should have a JOIN for the Joiner transform
        assert "JOIN" in self.model_sql

    def test_has_group_by(self):
        assert "GROUP BY" in self.model_sql

    def test_has_sum(self):
        assert "SUM(AMOUNT)" in self.model_sql or "sum(amount)" in self.model_sql.lower()

    def test_has_count(self):
        assert "COUNT(ORDER_ID)" in self.model_sql or "count(order_id)" in self.model_sql.lower()

    def test_has_cte_structure(self):
        assert "WITH" in self.model_sql
        assert "AS (" in self.model_sql
