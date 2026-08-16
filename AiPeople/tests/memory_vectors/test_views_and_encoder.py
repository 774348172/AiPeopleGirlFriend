from __future__ import annotations

import hashlib
import json
import math

import pytest

from runtime._memory_vectors import (
    BGE_MODEL_ID,
    EncoderAssetError,
    LocalBgeEncoder,
    MemoryVectorStore,
    VectorIntegrityError,
    build_selector_views,
)
from tests.memory_vectors._helpers import FakeEncoder, activate, commit_memory, open_ledger


def test_selector_views_are_deterministic_versioned_and_hashed(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        memory = activate(store, event)
        first = build_selector_views(memory)
        second = build_selector_views(memory)
        assert first == second
        assert [view.view_ordinal for view in first] == list(range(len(first)))
        assert first[0].view_kind == "statement"
        assert all(view.memory_version == 2 for view in first)
        assert all(
            view.text_sha256 == hashlib.sha256(view.text.encode("utf-8")).hexdigest()
            for view in first
        )
        assert len({view.source_revision for view in first}) == 1
    finally:
        ledger.close()


def test_non_indexable_memory_has_no_selector_views(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        _, store = commit_memory(ledger)
        assert build_selector_views(store.get_memory("memory-1")) == ()
    finally:
        ledger.close()


def test_runtime_normalizes_vectors_and_rejects_bad_encoder_output(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        activate(store, event)
        vectors = MemoryVectorStore(store._connection, FakeEncoder())
        vectors.rebuild_all()
        snapshot = vectors.matrix_snapshot()
        assert snapshot.rows == len(snapshot.views) > 0
        assert snapshot.dimension == 4
        assert all(math.isclose(math.sqrt(sum(x * x for x in snapshot.row(i))), 1.0, abs_tol=1e-5) for i in range(snapshot.rows))

        bad = FakeEncoder(dimension=5)
        bad.encode = lambda texts: [[1.0, float("nan"), 2.0, 3.0, 4.0] for _ in texts]
        with pytest.raises(VectorIntegrityError, match="non-finite"):
            MemoryVectorStore(store._connection, bad).rebuild_all()
    finally:
        ledger.close()


def test_local_bge_requires_explicit_local_asset_and_never_downloads(tmp_path) -> None:
    with pytest.raises(EncoderAssetError, match="does not exist"):
        LocalBgeEncoder(tmp_path / "missing", revision="r1")

    asset = tmp_path / "bge"
    asset.mkdir()
    (asset / "config.json").write_text('{"model":"bge"}', encoding="utf-8")
    (asset / "sentence_bert_config.json").write_text(
        json.dumps({"max_seq_length": 512}), encoding="utf-8"
    )
    pooling = asset / "1_Pooling"
    pooling.mkdir()
    (asset / "modules.json").write_text(
        json.dumps([{"type": "sentence_transformers.models.Pooling", "path": "1_Pooling"}]),
        encoding="utf-8",
    )
    (pooling / "config.json").write_text(
        json.dumps(
            {
                "word_embedding_dimension": 512,
                "pooling_mode_mean_tokens": True,
                "pooling_mode_cls_token": False,
            }
        ),
        encoding="utf-8",
    )
    paths = []

    class Backend:
        def encode(self, texts, **kwargs):
            return [[1.0] * 512 for _ in texts]

    encoder = LocalBgeEncoder(asset, revision="local-r1", loader=lambda path: paths.append(path) or Backend())
    assert encoder.identity.model_id == BGE_MODEL_ID
    assert paths == []
    assert len(encoder.encode(["测试"])[0]) == 512
    assert paths == [asset.resolve()]
