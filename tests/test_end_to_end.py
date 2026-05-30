"""End-to-end test: Informatica XML → IR → dbt project."""

import tempfile
from pathlib import Path

from pipeshift.generator import generate_dbt_project
from pipeshift.parser.informatica_xml import parse_file

SAMPLE_DIR = Path(__file__).parent / "sample_exports"


class TestEndToEnd:
    """Full pipeline: parse XML, generate dbt project, verify output."""

    def setup_method(self):
        self.repo = parse_file(SAMPLE_DIR / "customer_dim.xml")
        self.tmp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.tmp_dir) / "dbt_output"
        self.generated = generate_dbt_project(self.repo, self.output_dir)

    def test_generates_files(self):
        assert len(self.generated) >= 4  # dbt_project.yml, sources, model, schema

    def test_dbt_project_yml_exists(self):
        path = self.output_dir / "dbt_project.yml"
        assert path.exists()
        content = path.read_text()
        assert "name:" in content
        assert "sales_dw" in content

    def test_sources_yml_exists(self):
        path = self.output_dir / "models" / "_sources.yml"
        assert path.exists()
        content = path.read_text()
        assert "sources:" in content
        assert "customers" in content
        assert "region_mapping" in content

    def test_model_generated(self):
        path = self.output_dir / "models" / "m_customer_dim.sql"
        assert path.exists()
        content = path.read_text()
        # Should contain source reference
        assert "source(" in content
        # Should contain translated expressions
        assert "TRIM" in content  # from LTRIM(RTRIM(...))
        assert "CASE WHEN" in content  # from IIF(...)
        assert "CURRENT_TIMESTAMP" in content  # from SYSDATE

    def test_model_has_filter(self):
        path = self.output_dir / "models" / "m_customer_dim.sql"
        content = path.read_text()
        assert "WHERE" in content
        assert "INACTIVE" in content

    def test_model_has_lookup_join(self):
        path = self.output_dir / "models" / "m_customer_dim.sql"
        content = path.read_text()
        assert "LEFT JOIN" in content
        assert "lkp" in content
        assert "region_name" in content

    def test_schema_yml_exists(self):
        path = self.output_dir / "models" / "_schema.yml"
        assert path.exists()
        content = path.read_text()
        assert "models:" in content
        assert "m_customer_dim" in content
        assert "unique" in content
        assert "not_null" in content

    def test_model_sql_is_valid_structure(self):
        """Basic structural validation of generated SQL."""
        path = self.output_dir / "models" / "m_customer_dim.sql"
        content = path.read_text()
        # Should have WITH ... AS pattern (CTEs)
        assert "WITH" in content
        assert "AS (" in content
        # Should end with a final SELECT
        assert "SELECT" in content.split("\n")[-1] or "SELECT" in content.split("\n")[-2]
