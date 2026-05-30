"""Tests for analyzer: dependency graph and HTML report."""

from pathlib import Path

from pipeshift.analyzer import build_dependency_graph, generate_html_report
from pipeshift.ir.schema import Mapping, Repository, Source, Target
from pipeshift.parser.informatica_xml import parse_file

SAMPLE_DIR = Path(__file__).parent / "sample_exports"


class TestDependencyGraph:
    def test_independent_mappings(self):
        repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        graph = build_dependency_graph(repo)
        # Single mapping, no dependencies
        assert all(deps == [] for deps in graph.values())

    def test_detects_dependency(self):
        # Mapping A writes to TARGET_X, Mapping B reads from SOURCE TARGET_X
        repo = Repository(
            name="test",
            mappings=[
                Mapping(
                    id="m_a", name="m_a",
                    sources=[Source(id="RAW", name="RAW", type="relational")],
                    targets=[Target(id="STAGING", name="STAGING", type="relational")],
                    transforms=[],
                ),
                Mapping(
                    id="m_b", name="m_b",
                    sources=[Source(id="STAGING", name="STAGING", type="relational")],
                    targets=[Target(id="MART", name="MART", type="relational")],
                    transforms=[],
                ),
            ],
        )
        graph = build_dependency_graph(repo)
        assert "m_b" in graph["m_a"]
        assert graph["m_b"] == []


class TestHTMLReport:
    def test_generates_html(self):
        repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        html = generate_html_report(repo)
        assert "<!DOCTYPE html>" in html
        assert "PipeShift Migration Assessment" in html

    def test_contains_repo_name(self):
        repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        html = generate_html_report(repo)
        assert "DEV_REPO" in html

    def test_contains_complexity_table(self):
        repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        html = generate_html_report(repo)
        assert "simple" in html
        assert "Complexity Distribution" in html

    def test_contains_mapping_table(self):
        repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        html = generate_html_report(repo)
        assert "m_customer_dim" in html

    def test_contains_workflow_info(self):
        repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        html = generate_html_report(repo)
        assert "wf_customer_dim_daily" in html
        assert "0 2 * * *" in html
