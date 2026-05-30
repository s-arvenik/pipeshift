"""Tests for SCD Type 2 parsing and dbt snapshot generation."""

import tempfile
from pathlib import Path

from pipeshift.generator import generate_dbt_project
from pipeshift.ir.schema import SCDType, TransformType
from pipeshift.parser.informatica_xml import parse_file

SAMPLE_DIR = Path(__file__).parent / "sample_exports"


class TestSCDParsing:
    def setup_method(self):
        self.repo = parse_file(SAMPLE_DIR / "scd2_product.xml")
        self.mapping = self.repo.mappings[0]

    def test_scd_transform_detected(self):
        scd_transforms = [t for t in self.mapping.transforms if t.type == TransformType.SCD]
        assert len(scd_transforms) == 1

    def test_scd_config_populated(self):
        scd = next(t for t in self.mapping.transforms if t.type == TransformType.SCD)
        assert scd.scd_config is not None
        assert scd.scd_config.scd_type == SCDType.TYPE_2

    def test_scd_key_columns(self):
        scd = next(t for t in self.mapping.transforms if t.type == TransformType.SCD)
        assert scd.scd_config.key_columns == ["PRODUCT_ID"]

    def test_scd_tracked_columns(self):
        scd = next(t for t in self.mapping.transforms if t.type == TransformType.SCD)
        assert scd.scd_config.tracked_columns == ["PRODUCT_NAME", "CATEGORY", "PRICE"]

    def test_scd_date_fields(self):
        scd = next(t for t in self.mapping.transforms if t.type == TransformType.SCD)
        assert scd.scd_config.effective_date_field == "EFF_START_DATE"
        assert scd.scd_config.end_date_field == "EFF_END_DATE"

    def test_scd_current_flag(self):
        scd = next(t for t in self.mapping.transforms if t.type == TransformType.SCD)
        assert scd.scd_config.current_flag_field == "CURRENT_FLAG"


class TestSnapshotGeneration:
    def setup_method(self):
        self.repo = parse_file(SAMPLE_DIR / "scd2_product.xml")
        self.tmp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.tmp_dir) / "dbt_output"
        self.generated = generate_dbt_project(self.repo, self.output_dir)

    def test_snapshot_file_created(self):
        snap_dir = self.output_dir / "snapshots"
        assert snap_dir.exists()
        snap_files = list(snap_dir.glob("*.sql"))
        assert len(snap_files) == 1

    def test_snapshot_name(self):
        snap_file = list((self.output_dir / "snapshots").glob("*.sql"))[0]
        assert "snap_" in snap_file.name
        assert "scd2_product" in snap_file.name

    def test_snapshot_has_jinja_block(self):
        snap_file = list((self.output_dir / "snapshots").glob("*.sql"))[0]
        content = snap_file.read_text()
        assert "{% snapshot" in content
        assert "{% endsnapshot %}" in content

    def test_snapshot_has_config(self):
        snap_file = list((self.output_dir / "snapshots").glob("*.sql"))[0]
        content = snap_file.read_text()
        assert "config(" in content
        assert "unique_key=" in content
        assert "strategy=" in content

    def test_snapshot_uses_timestamp_strategy(self):
        snap_file = list((self.output_dir / "snapshots").glob("*.sql"))[0]
        content = snap_file.read_text()
        assert "strategy='timestamp'" in content
        assert "updated_at='eff_start_date'" in content

    def test_snapshot_has_unique_key(self):
        snap_file = list((self.output_dir / "snapshots").glob("*.sql"))[0]
        content = snap_file.read_text()
        assert "product_id" in content

    def test_snapshot_selects_columns(self):
        snap_file = list((self.output_dir / "snapshots").glob("*.sql"))[0]
        content = snap_file.read_text()
        assert "product_name" in content
        assert "category" in content
        assert "price" in content

    def test_snapshot_has_source_ref(self):
        snap_file = list((self.output_dir / "snapshots").glob("*.sql"))[0]
        content = snap_file.read_text()
        assert "source(" in content

    def test_no_model_generated_for_scd(self):
        """SCD mappings should generate snapshots, not models."""
        models_dir = self.output_dir / "models"
        model_files = list(models_dir.glob("m_scd2*.sql"))
        assert len(model_files) == 0
