from __future__ import annotations

import json

from runtime import Completed, RelationshipRuntime, RuntimeConfig
from runtime.adapters import FakeReplyModel
from tests.memory_scheduler._helpers import collect, message


class EmptyProposeModel:
    def __init__(self) -> None:
        self.requests = []
        self.messages = []

    async def generate_memory_propose(self, request, messages):
        self.requests.append(request)
        self.messages.append(messages)
        return json.dumps(
            {
                "schema_version": 1,
                "mode": "MEMORY_PROPOSE",
                "proposal_run_id": request.proposal_run_id,
                "proposals": [],
            },
            ensure_ascii=False,
        )


class ValidProposeModel(EmptyProposeModel):
    async def generate_memory_propose(self, request, messages):
        self.requests.append(request)
        self.messages.append(messages)
        source = request.events[0]
        return json.dumps(
            {
                "schema_version": 1,
                "mode": "MEMORY_PROPOSE",
                "proposal_run_id": request.proposal_run_id,
                "proposals": [
                    {
                        "kind": "player_fact",
                        "statement": source.text,
                        "subject": {
                            "type": "player",
                            "entity_id": None,
                            "display_name": None,
                        },
                        "epistemic": {
                            "polarity": "affirmed",
                            "modality": "asserted",
                            "grounding": "speaker_report",
                        },
                        "temporal": {
                            "relation": "atemporal",
                            "resolution": "not_applicable",
                            "source_text": None,
                            "anchor_event_id": None,
                            "start_at": None,
                            "end_at": None,
                            "timezone": None,
                            "precision": "not_applicable",
                        },
                        "evidence_quotes": [
                            {
                                "event_id": source.event_id,
                                "role": "support",
                                "quote": source.text,
                                "start_hint": 0,
                            }
                        ],
                        "semantic_reason": "玩家明确表达的跨回合事实",
                        "confidence": 0.9,
                        "relation_suggestions": {
                            "semantic_slot": "player.test.fact",
                            "supersedes": [],
                            "contradicts": [],
                            "refines": [],
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )


async def test_builtin_pipeline_commits_empty_result_without_visible_output(tmp_path) -> None:
    proposer = EmptyProposeModel()
    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path),
        FakeReplyModel(["可见回复"]),
        memory_propose_model=proposer,
    ) as runtime:
        events = await collect(runtime, message(1))
        await runtime.wait_for_memory_background_idle()
        assert isinstance(events[-1], Completed)
        assert events[-1].text == "可见回复"
        assert runtime.memory_background_snapshot().completed == 1
        request = proposer.requests[0]
        run = runtime._ledger.memory_store()._require_run(request.proposal_run_id)
        assert run.state == "committed"
        assert runtime._ledger.memory_store().list_memories() == ()
        assert proposer.messages[0][0]["role"] == "system"
        assert proposer.messages[0][1]["role"] == "user"


async def test_builtin_pipeline_materializes_and_commits_proposed_memory(tmp_path) -> None:
    proposer = ValidProposeModel()
    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path),
        FakeReplyModel(["记住了。"]),
        memory_propose_model=proposer,
    ) as runtime:
        await collect(runtime, message(1))
        await runtime.wait_for_memory_background_idle()
        memories = runtime._ledger.memory_store().list_memories()
        assert len(memories) == 1
        assert memories[0].status == "proposed"
        assert memories[0].statement == "第1条消息"
        assert memories[0].evidence[0].event_id == proposer.requests[0].events[0].event_id


async def test_invalid_model_json_is_terminal_and_preserves_committed_messages(tmp_path) -> None:
    class InvalidModel:
        async def generate_memory_propose(self, request, messages):
            return "not json"

    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path),
        FakeReplyModel(["回复仍然提交"]),
        memory_propose_model=InvalidModel(),
    ) as runtime:
        result = await collect(runtime, message(1))
        await runtime.wait_for_memory_background_idle()
        assert isinstance(result[-1], Completed)
        assert runtime.memory_background_snapshot().failed_terminal == 1
        assert len(runtime._ledger.list_events("memory-scheduler")) == 2
        run = runtime._ledger.memory_store()._connection.execute(
            "SELECT state, failure_code FROM memory_proposal_runs"
        ).fetchone()
        assert tuple(run) == ("failed_terminal", "memory_propose_invalid_json")
