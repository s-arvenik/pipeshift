"""Tests for ref() between models and reconciliation test generation."""

import tempfile
from pathlib import Path

from pipeshift.generator import generate_dbt_project
from pipeshift.ir.schema import Mapping, Repository, Source, Target, Transform, TransformType


class TestRefBetweenModels:
    def test_ref_generated_when_source_is_another_target(self):
        """Mapping B reads from STAGING which is Mapping A's target → ref('m_a')."""
        repo = Repository(
            name="test",
            sources=[Source(id="RAW", name="RAW", type="relational", connection="db")],
            targets=[],
            mappings=[
                Mapping(
                    id="m_a", name="m_a",
                    sources=[Source(id="RAW", name="RAW", type="relational", connection="db")],
                    targets=[Target(id="STAGING", name="STAGING", type="relational")],
                    transforms=[
                        Transform(id="SQ_RAW", name="SQ_RAW", type=TransformType.SOURCE_QUALIFIER),
                    ],
                ),
                Mapping(
                    id="m_b", name="m_b",
                    sources=[Source(id="STAGING", name="STAGING", type="relational")],
                    targets=[Target(id="MART", name="MART", type="relational")],
                    transforms=[
                        Transform(id="SQ_STAGING", name="SQ_STAGING", type=TransformType.SOURCE_QUALIFIER),
                    ],
                ),
            ],
        )
        out = Path(tempfile.mkdtemp()) / "dbt"
        generate_dbt_project(repo, out)

        # m_b should use ref('m_a') since STAGING is m_a's target
        m_b_sql = (out / "models" / "m_b.sql").read_text()
        assert "ref('m_a')" in m_b_sql

        # m_a should use source() since RAW is not another mapping's target
        m_a_sql = (out / "models" / "m_a.sql").read_text()
        assert "source(" in m_a_sql
        assert "ref(" not in m_a_sql

    def test_no_self_reference(self):
        """A mapping should not ref() itself."""
        repo = Repository(
            name="test",
            sources=[],
            targets=[],
            mappings=[
                Mapping(
                    id="m_x", name="m_x",
                    sources=[Source(id="X_TABLE", name="X_TABLE", type="relational")],
                    targets=[Target(id="X_TABLE", name="X_TABLE", type="relational")],
                    transforms=[
                        Transform(id="SQ_X_TABLE", name="SQ_X_TABLE", type=TransformType.SOURCE_QUALIFIER),
                    ],
                ),
            ],
        )
        out = Path(tempfile.mkdtemp()) / "dbt"
        generate_dbt_project(repo, out)
        sql = (out / "models" / "m_x.sql").read_text()
        assert "ref(" not in sql


class TestReconciliationTests:
    def test_recon_file_generated(self):
        repo = Repository(
            name="test",
            sources=[Source(id="SRC", name="SRC", type="relational", connection="db")],
            targets=[Target(id="TGT", name="TGT", type="relational")],
            mappings=[
                Mapping(
                    id="m_load", name="m_load",
                    sources=[Source(id="SRC", name="SRC", type="relational", connection="db")],
                    targets=[Target(id="TGT", name="TGT", type="relational")],
                    transforms=[
                        Transform(id="SQ_SRC", name="SQ_SRC", type=TransformType.SOURCE_QUALIFIER),
                    ],
                ),
            ],
        )
        out = Path(tempfile.mkdtemp()) / "dbt"
        generate_dbt_project(repo, out)
        recon_dir = out / "tests" / "reconciliation"
        assert recon_dir.exists()
        recon_files = list(recon_dir.glob("*.sql"))
        assert len(recon_files) == 1
        assert "recon_m_load.sql" == recon_files[0].name

    def test_recon_content(self):
        repo = Repository(
            name="test",
            sources=[Source(id="ORDERS", name="ORDERS", type="relational", connection="sales_db")],
            targets=[Target(id="DIM", name="DIM", type="relational")],
            mappings=[
                Mapping(
                    id="m_orders", name="m_orders",
                    sources=[Source(id="ORDERS", name="ORDERS", type="relational", connection="sales_db")],
                    targets=[Target(id="DIM", name="DIM", type="relational")],
                    transforms=[
                        Transform(id="SQ_ORDERS", name="SQ_ORDERS", type=TransformType.SOURCE_QUALIFIER),
                    ],
                ),
            ],
        )
        out = Path(tempfile.mkdtemp()) / "dbt"
        generate_dbt_project(repo, out)
        content = (out / "tests" / "reconciliation" / "recon_m_orders.sql").read_text()
        assert "source('sales_db', 'orders')" in content
        assert "ref('m_orders')" in content
        assert "COUNT(*)" in content
        assert "WHERE s.cnt != t.cnt" in content
