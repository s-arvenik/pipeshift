"""Tests for Rank, Sequence Generator, and Update Strategy transforms."""

from pipeshift.generator import generate_dbt_project
from pipeshift.ir.schema import (
    LoadType, Mapping, Repository, Source, Target, Transform, TransformType,
)

import tempfile
from pathlib import Path


def _generate_model(mapping: Mapping, sources=None, targets=None) -> str:
    """Helper: generate a dbt model from a mapping and return the SQL."""
    repo = Repository(
        name="test",
        sources=sources or [],
        targets=targets or [],
        mappings=[mapping],
    )
    out = Path(tempfile.mkdtemp()) / "dbt"
    generate_dbt_project(repo, out)
    model_files = list((out / "models").glob("*.sql"))
    assert len(model_files) == 1
    return model_files[0].read_text()


class TestRankTransform:
    def test_rank_generates_row_number(self):
        mapping = Mapping(
            id="m_rank", name="m_rank",
            sources=[Source(id="ORDERS", name="ORDERS", type="relational")],
            targets=[],
            transforms=[
                Transform(id="SQ_ORDERS", name="SQ_ORDERS", type=TransformType.SOURCE_QUALIFIER),
                Transform(
                    id="RNK_TOP", name="RNK_TOP", type=TransformType.RANK,
                    properties={"Group By Ports": "CUSTOMER_ID", "Order By Ports": "AMOUNT DESC"},
                ),
            ],
        )
        sql = _generate_model(mapping)
        assert "ROW_NUMBER()" in sql
        assert "PARTITION BY customer_id" in sql
        assert "ORDER BY amount desc" in sql

    def test_rank_without_group_by(self):
        mapping = Mapping(
            id="m_rank2", name="m_rank2",
            sources=[Source(id="DATA", name="DATA", type="relational")],
            targets=[],
            transforms=[
                Transform(id="SQ_DATA", name="SQ_DATA", type=TransformType.SOURCE_QUALIFIER),
                Transform(
                    id="RNK", name="RNK", type=TransformType.RANK,
                    properties={"Rank Port": "SCORE"},
                ),
            ],
        )
        sql = _generate_model(mapping)
        assert "ROW_NUMBER()" in sql
        assert "ORDER BY score" in sql


class TestSequenceGenerator:
    def test_generates_row_number(self):
        mapping = Mapping(
            id="m_seq", name="m_seq",
            sources=[Source(id="SRC", name="SRC", type="relational")],
            targets=[],
            transforms=[
                Transform(id="SQ_SRC", name="SQ_SRC", type=TransformType.SOURCE_QUALIFIER),
                Transform(
                    id="SEQ", name="SEQ", type=TransformType.SEQUENCE_GENERATOR,
                    properties={"Start Value": "1"},
                ),
            ],
        )
        sql = _generate_model(mapping)
        assert "ROW_NUMBER()" in sql
        assert "generated_id" in sql

    def test_start_value_offset(self):
        mapping = Mapping(
            id="m_seq2", name="m_seq2",
            sources=[Source(id="SRC", name="SRC", type="relational")],
            targets=[],
            transforms=[
                Transform(id="SQ_SRC", name="SQ_SRC", type=TransformType.SOURCE_QUALIFIER),
                Transform(
                    id="SEQ", name="SEQ", type=TransformType.SEQUENCE_GENERATOR,
                    properties={"Start Value": "1000"},
                ),
            ],
        )
        sql = _generate_model(mapping)
        assert "+ 999" in sql  # 1000 - 1 = 999 offset


class TestUpdateStrategy:
    def test_incremental_config_added(self):
        mapping = Mapping(
            id="m_upsert", name="m_upsert",
            sources=[Source(id="SRC", name="SRC", type="relational")],
            targets=[Target(id="TGT", name="TGT", type="relational", key_columns=["ID"])],
            transforms=[
                Transform(id="SQ_SRC", name="SQ_SRC", type=TransformType.SOURCE_QUALIFIER),
                Transform(id="UPD", name="UPD", type=TransformType.UPDATE_STRATEGY),
            ],
        )
        sql = _generate_model(mapping)
        assert "materialized='incremental'" in sql
        assert "incremental_strategy='merge'" in sql
        assert "unique_key=" in sql

    def test_unique_key_from_target(self):
        mapping = Mapping(
            id="m_upsert2", name="m_upsert2",
            sources=[Source(id="SRC", name="SRC", type="relational")],
            targets=[Target(id="TGT", name="TGT", type="relational", key_columns=["CUSTOMER_ID"])],
            transforms=[
                Transform(id="SQ_SRC", name="SQ_SRC", type=TransformType.SOURCE_QUALIFIER),
                Transform(id="UPD", name="UPD", type=TransformType.UPDATE_STRATEGY),
            ],
        )
        sql = _generate_model(mapping)
        assert "'customer_id'" in sql


class TestNormalizerTransform:
    def test_normalizer_generates_unpivot(self):
        mapping = Mapping(
            id="m_norm", name="m_norm",
            sources=[Source(id="SRC", name="SRC", type="relational")],
            targets=[],
            transforms=[
                Transform(id="SQ_SRC", name="SQ_SRC", type=TransformType.SOURCE_QUALIFIER),
                Transform(
                    id="NRM", name="NRM", type=TransformType.NORMALIZER,
                    properties={"Normalize Columns": "JAN_SALES, FEB_SALES, MAR_SALES"},
                ),
            ],
        )
        sql = _generate_model(mapping)
        assert "normalized" in sql
        assert "unpivoted" in sql
        assert "jan_sales" in sql
        assert "feb_sales" in sql
        assert "column_name" in sql
        assert "column_value" in sql

    def test_normalizer_without_columns_adds_todo(self):
        mapping = Mapping(
            id="m_norm2", name="m_norm2",
            sources=[Source(id="SRC", name="SRC", type="relational")],
            targets=[],
            transforms=[
                Transform(id="SQ_SRC", name="SQ_SRC", type=TransformType.SOURCE_QUALIFIER),
                Transform(id="NRM", name="NRM", type=TransformType.NORMALIZER, properties={}),
            ],
        )
        sql = _generate_model(mapping)
        assert "TODO" in sql
