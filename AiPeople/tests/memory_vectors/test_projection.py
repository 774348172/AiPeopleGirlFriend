from __future__ import annotations

import pytest

from runtime._memory_vectors import MemoryVectorStore, VectorIntegrityError
from tests.memory_vectors._helpers import FakeEncoder, activate, commit_memory, open_ledger


def test_empty_projection_is_a_valid_complete_generation(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        vectors = MemoryVectorStore.from_memory_store(ledger.memory_store(), FakeEncoder())
        result = vectors.rebuild_all()
        snapshot = vectors.matrix_snapshot()
        assert result.rebuilt is True
        assert snapshot.rows == 0
        assert snapshot.dimension == 4
        assert snapshot.generation == result.generation
    finally:
        ledger.close()


def test_incremental_add_version_change_and_status_invalidation(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        vectors = MemoryVectorStore(store._connection, FakeEncoder())
        vectors.rebuild_all()
        assert vectors.matrix_snapshot().rows == 0

        active = activate(store, event)
        added = vectors.sync_incremental()
        assert added.embedded_memories == 1
        assert {view.memory_version for view in vectors.matrix_snapshot().views} == {active.version}

        disputed = store.decide_memory(
            memory_id=active.memory_id,
            to_status="disputed",
            reason_code="conflict_detected",
            idempotency_key="dispute-memory-1",
        )
        changed = vectors.sync_incremental()
        assert changed.embedded_memories == 1
        assert {view.memory_version for view in vectors.matrix_snapshot().views} == {disputed.version}

        store.decide_memory(
            memory_id=active.memory_id,
            to_status="rejected",
            reason_code="evidence_rejected",
            idempotency_key="reject-memory-1",
        )
        assert vectors.matrix_snapshot().rows == 0
        removed = vectors.sync_incremental()
        assert removed.invalidated_memories == 1
    finally:
        ledger.close()


def test_encoder_or_view_profile_drift_forces_full_generation_rebuild(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        activate(store, event)
        first = MemoryVectorStore(store._connection, FakeEncoder()).rebuild_all()
        changed = MemoryVectorStore(
            store._connection,
            FakeEncoder(revision="test-revision-2", artifact_seed="asset-2"),
            view_profile_id="mem05-selector-view-v2-test",
        ).sync_incremental()
        assert changed.rebuilt is True
        assert changed.generation.generation_id != first.generation.generation_id
        assert changed.generation.encoder.revision == "test-revision-2"
    finally:
        ledger.close()


def test_full_rebuild_matches_incremental_content(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        activate(store, event)
        vectors = MemoryVectorStore(store._connection, FakeEncoder())
        vectors.sync_incremental()
        before = vectors.matrix_snapshot()
        expected = [(view.memory_id, view.view_kind, view.text, before.row(i)) for i, view in enumerate(before.views)]
        rebuilt = vectors.rebuild_all()
        after = vectors.matrix_snapshot()
        actual = [(view.memory_id, view.view_kind, view.text, after.row(i)) for i, view in enumerate(after.views)]
        assert rebuilt.rebuilt is True
        assert actual == expected
    finally:
        ledger.close()


@pytest.mark.parametrize("fault_point", ["encoded", "staged", "activated"])
def test_rebuild_crash_keeps_previous_complete_generation(tmp_path, fault_point) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        activate(store, event)
        vectors = MemoryVectorStore(store._connection, FakeEncoder())
        previous = vectors.rebuild_all().generation

        def fail(point):
            if point == fault_point:
                raise RuntimeError("injected rebuild crash")

        with pytest.raises(RuntimeError, match="injected"):
            vectors.rebuild_all(fault_hook=fail)
        assert vectors.active_generation() == previous
        assert vectors.matrix_snapshot().rows > 0
    finally:
        ledger.close()


@pytest.mark.parametrize("corruption", ["blob", "hash"])
def test_corrupt_vector_blob_or_hash_is_rejected(tmp_path, corruption) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        activate(store, event)
        vectors = MemoryVectorStore(store._connection, FakeEncoder())
        generation = vectors.rebuild_all().generation
        if corruption == "blob":
            store._connection.execute(
                "UPDATE memory_embeddings SET vector_blob=x'0000' WHERE generation_id=?",
                (generation.generation_id,),
            )
        else:
            store._connection.execute(
                "UPDATE memory_embeddings SET vector_sha256=? WHERE generation_id=?",
                ("0" * 64, generation.generation_id),
            )
        store._connection.commit()
        with pytest.raises(VectorIntegrityError):
            vectors.matrix_snapshot()
    finally:
        ledger.close()
