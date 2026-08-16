from __future__ import annotations

from datetime import datetime, timezone
import asyncio

from runtime import Completed, RelationshipRuntime, RuntimeConfig, UserMessage
from runtime._ledger import EventLedger
from runtime._memory_aware_model import MemoryAwareReplyModel
from runtime._memory_selection import MemorySelectionPipeline
from runtime._memory_vectors import MemoryVectorStore
from runtime._prompt import build_reply_messages
from runtime._reranker import FakeMemoryReranker
from runtime._selected_memory import SelectedMemoryAssembler
from runtime.adapters import FakeReplyModel
from tests.memory_commit._helpers import append_evidence, batch, begin, memory_for
from tests.memory_e2e._helpers import BgeSizedFakeEncoder


NOW = datetime(2026, 8, 8, 4, 0, tzinfo=timezone.utc)


def seed_active_memory(
    ledger: EventLedger,
    *,
    statement: str = "玩家和秦未晞说好有空一起看那部老电影。",
):
    event = append_evidence(ledger, request_id="seed-request")
    store = ledger.memory_store()
    begin(store, event, proposal_run_id="seed-run", key="seed-begin")
    memory = memory_for(
        event,
        memory_id="memory-1",
        proposal_run_id="seed-run",
        statement=statement,
    )
    store.commit_batch(
        batch(memory, proposal_run_id="seed-run"), idempotency_key="seed-commit"
    )
    active = store.decide_memory(
        memory_id="memory-1",
        to_status="active",
        reason_code="evidence_validated",
        idempotency_key="seed-activate",
        source_event_id=event.event_id,
    )
    return event, store, active


def make_runtime(
    tmp_path,
    *,
    score: float = 0.95,
    fail=None,
    statement: str = "玩家和秦未晞说好有空一起看那部老电影。",
    reranker_override=None,
    rerank_timeout_seconds: float = 0.25,
):
    config = RuntimeConfig(tmp_path)
    ledger = EventLedger.open(config.database_path, clock=lambda: NOW)
    _, store, memory = seed_active_memory(ledger, statement=statement)
    encoder = BgeSizedFakeEncoder()
    vectors = MemoryVectorStore(store._connection, encoder)
    vectors.rebuild_all()
    reranker = reranker_override or FakeMemoryReranker(
        {memory.memory_id: score}, fail=fail
    )
    selector = MemorySelectionPipeline(
        vector_store=vectors,
        query_encoder=encoder,
        query_tokenizer=encoder,
        reranker=reranker,
        rerank_timeout_seconds=rerank_timeout_seconds,
    )
    base_model = FakeReplyModel(["今晚正好，", "把那部老电影补上？"])
    model = MemoryAwareReplyModel(
        base_model,
        selector,
        SelectedMemoryAssembler.from_memory_store(store),
    )
    runtime = RelationshipRuntime(config, model, ledger, lambda: NOW)
    return runtime, base_model, model, reranker


async def collect(runtime, message):
    return [event async for event in runtime.handle_turn(message)]


def user_message(request_id="turn-1"):
    return UserMessage(
        request_id,
        "conversation-live",
        "今晚突然空下来了。",
        NOW,
        "Asia/Shanghai",
    )


async def test_runtime_uses_selected_long_term_memory_in_exactly_one_reply(tmp_path) -> None:
    runtime, base_model, model, reranker = make_runtime(tmp_path)
    async with runtime:
        events = await collect(runtime, user_message())
    assert isinstance(events[-1], Completed)
    assert base_model.calls == 1
    assert len(reranker.calls) == 1
    context = base_model.requests[0].context
    frame = context.selected_memory_frame
    assert frame is not None
    assert frame.source_memory_ids == ("memory-1",)
    assert frame.source_event_ids
    assert 0 < frame.token_count <= 256
    dynamic = build_reply_messages(context)[-2]["content"]
    assert "玩家和秦未晞说好有空一起看那部老电影" in dynamic
    assert "activation_score" not in dynamic
    assert "memory-1" not in dynamic
    assert model.selection_audits[0].status == "complete"
    assert model.selection_audits[0].selected_memory_count == 1


async def test_all_reject_produces_empty_frame_and_still_one_reply(tmp_path) -> None:
    runtime, base_model, model, reranker = make_runtime(tmp_path, score=0.1)
    async with runtime:
        events = await collect(runtime, user_message())
    assert isinstance(events[-1], Completed)
    assert base_model.calls == 1
    assert len(reranker.calls) == 1
    frame = base_model.requests[0].context.selected_memory_frame
    assert frame is not None and frame.selected_memories == ()
    assert "本轮选中的长期记忆" not in build_reply_messages(
        base_model.requests[0].context
    )[-2]["content"]


async def test_reranker_failure_degrades_to_empty_frame_without_second_reply(tmp_path) -> None:
    runtime, base_model, model, reranker = make_runtime(
        tmp_path, fail=RuntimeError("configured reranker crash")
    )
    async with runtime:
        events = await collect(runtime, user_message())
    assert isinstance(events[-1], Completed)
    assert base_model.calls == 1
    assert len(reranker.calls) == 1
    assert base_model.requests[0].context.selected_memory_frame.selected_memories == ()
    assert model.selection_audits[0].status == "degraded"
    assert model.selection_audits[0].failure_code == "memory_selection_failed"


async def test_reranker_timeout_degrades_to_empty_frame(tmp_path) -> None:
    class SlowReranker(FakeMemoryReranker):
        async def rerank(self, batch):
            self.calls.append(batch)
            await asyncio.sleep(1.0)

    slow = SlowReranker({"memory-1": 0.95})
    runtime, base_model, model, _ = make_runtime(
        tmp_path,
        reranker_override=slow,
        rerank_timeout_seconds=0.01,
    )
    async with runtime:
        events = await collect(runtime, user_message())
    assert isinstance(events[-1], Completed)
    assert base_model.calls == 1
    assert model.selection_audits[0].failure_code == "reranker_timeout"
    assert base_model.requests[0].context.selected_memory_frame.selected_memories == ()


async def test_selected_memory_budget_drops_complete_oversized_entry(tmp_path) -> None:
    statement = "长记忆" * 130
    runtime, base_model, model, _ = make_runtime(tmp_path, statement=statement)
    async with runtime:
        events = await collect(runtime, user_message())
    assert isinstance(events[-1], Completed)
    frame = base_model.requests[0].context.selected_memory_frame
    assert frame.selected_memories == ()
    assert frame.truncated is True
    assert model.selection_audits[0].selected_memory_tokens == 0


async def test_stale_memory_version_is_not_injected_or_replaced(tmp_path) -> None:
    runtime, base_model, model, reranker = make_runtime(tmp_path)
    store = runtime._ledger.memory_store()
    memory = store.get_memory("memory-1")
    store.decide_memory(
        memory_id="memory-1",
        to_status="disputed",
        reason_code="conflict_detected",
        idempotency_key="e2e-dispute",
        source_event_id=memory.evidence[0].event_id,
    )
    async with runtime:
        events = await collect(runtime, user_message())
    assert isinstance(events[-1], Completed)
    assert reranker.calls == []
    assert base_model.requests[0].context.selected_memory_frame.selected_memories == ()
    assert model.selection_audits[0].status == "empty_pool"


async def test_restart_rebuilds_online_selection_from_persisted_projection(tmp_path) -> None:
    runtime, base_model, _, _ = make_runtime(tmp_path)
    async with runtime:
        first = await collect(runtime, user_message("turn-first"))
    assert isinstance(first[-1], Completed)

    config = RuntimeConfig(tmp_path)
    ledger = EventLedger.open(config.database_path, clock=lambda: NOW)
    store = ledger.memory_store()
    encoder = BgeSizedFakeEncoder()
    vectors = MemoryVectorStore(store._connection, encoder)
    assert vectors.active_generation() is not None
    reranker = FakeMemoryReranker({"memory-1": 0.95})
    selector = MemorySelectionPipeline(
        vector_store=vectors,
        query_encoder=encoder,
        query_tokenizer=encoder,
        reranker=reranker,
    )
    second_base = FakeReplyModel(["还记得。"])
    wrapper = MemoryAwareReplyModel(
        second_base, selector, SelectedMemoryAssembler.from_memory_store(store)
    )
    second_runtime = RelationshipRuntime(config, wrapper, ledger, lambda: NOW)
    async with second_runtime:
        second = await collect(second_runtime, user_message("turn-second"))
    assert isinstance(second[-1], Completed)
    assert second_base.requests[0].context.selected_memory_frame.source_memory_ids == (
        "memory-1",
    )
