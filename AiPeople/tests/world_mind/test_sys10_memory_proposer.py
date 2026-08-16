from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from runtime.world_mind import (
    HeroineMemoryProposeRequest,
    HeroineMemorySourceEvent,
    LlamaCppHeroineMemoryProposer,
    ModelAttemptAudit,
    V6MemoryProposeError,
    WorldMindModelIdentity,
)


class ScriptedBackend:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def complete_chat(self, **kwargs) -> str:
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _identity(character_id: str = "baiweixi") -> WorldMindModelIdentity:
    return WorldMindModelIdentity(
        model_id="test/model",
        revision="sys10-v1",
        artifact_sha256="a" * 64,
        character_id=character_id,
        world_id="songjiangfu",
        protagonist_id="protagonist",
    )


def _request() -> HeroineMemoryProposeRequest:
    return HeroineMemoryProposeRequest(
        save_id="save_001",
        world_id="songjiangfu",
        owner_character_id="baiweixi",
        source_events=(
            HeroineMemorySourceEvent(
                event_id="event-user",
                request_id="request-1",
                conversation_id="conversation-1",
                sequence_no=1,
                actor="protagonist",
                event_type="utterance",
                text="我喜欢这样的雨夜。",
                game_time=datetime(1, 10, 11, 18, 0),
            ),
        ),
        existing_memories=(),
    )


def _result(evidence_alias: int = 0):
    return {"m": [{"k": 1, "s": "男主喜欢雨夜", "e": [evidence_alias]}]}


def test_v6_memory_proposer_binds_repository_and_evidence_schema() -> None:
    async def scenario() -> None:
        audits: list[ModelAttemptAudit] = []
        backend = ScriptedBackend([_result()])
        proposer = LlamaCppHeroineMemoryProposer(
            backend,
            identity=_identity(),
            character_prompt="你是白未晞。",
            attempt_observer=audits.append,
        )
        drafts = await proposer.propose(_request())
        assert drafts[0].statement == "男主喜欢雨夜"
        call = backend.calls[0]
        payload = json.loads(call["messages"][-1]["content"])
        assert payload == {
            "p": "R2",
            "n": "D11 18:00",
            "e": [[0, 1, "我喜欢这样的雨夜。"]],
        }
        schema = call["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["m"]["maxItems"] == 2
        assert call["response_format"]["json_schema"]["name"] == "heroine_memory_r2"
        assert audits == [
            ModelAttemptAudit(
                request_id="save_001:baiweixi:event-user",
                mode="MEMORY_PROPOSE",
                attempt=0,
                success=True,
                failure_code=None,
            )
        ]

    asyncio.run(scenario())


def test_v6_memory_proposer_retries_invalid_json_and_reports_failure_code() -> None:
    async def scenario() -> None:
        audits: list[ModelAttemptAudit] = []
        backend = ScriptedBackend(["not-json", "still-not-json"])
        proposer = LlamaCppHeroineMemoryProposer(
            backend,
            identity=_identity(),
            character_prompt="你是白未晞。",
            attempt_observer=audits.append,
            retries=1,
            fail_closed=False,
        )
        with pytest.raises(V6MemoryProposeError) as captured:
            await proposer.propose(_request())
        assert captured.value.code == "model_invalid_json"
        assert [item.failure_code for item in audits] == [
            "model_invalid_json",
            "model_invalid_json",
        ]

    asyncio.run(scenario())


def test_v6_memory_proposer_rejects_evidence_outside_input() -> None:
    async def scenario() -> None:
        backend = ScriptedBackend([_result(99), _result(99)])
        proposer = LlamaCppHeroineMemoryProposer(
            backend,
            identity=_identity(),
            character_prompt="你是白未晞。",
            fail_closed=False,
        )
        with pytest.raises(V6MemoryProposeError) as captured:
            await proposer.propose(_request())
        assert captured.value.code == "model_contract_invalid"

    asyncio.run(scenario())


def test_v6_memory_proposer_fail_closed_returns_empty_and_records_failure() -> None:
    async def scenario() -> None:
        backend = ScriptedBackend(["not-json", "still-not-json"])
        proposer = LlamaCppHeroineMemoryProposer(
            backend,
            identity=_identity(),
            character_prompt="你是白未晞。",
            retries=1,
        )
        assert await proposer.propose(_request()) == ()
        assert proposer.last_failure is not None
        assert proposer.last_failure.code == "model_invalid_json"

    asyncio.run(scenario())


def test_v6_memory_proposer_rejects_wrong_character_before_model_call() -> None:
    async def scenario() -> None:
        backend = ScriptedBackend([_result()])
        proposer = LlamaCppHeroineMemoryProposer(
            backend,
            identity=_identity("another_heroine"),
            character_prompt="另一个角色。",
        )
        with pytest.raises(V6MemoryProposeError) as captured:
            await proposer.propose(_request())
        assert captured.value.code == "model_contract_invalid"
        assert backend.calls == []

    asyncio.run(scenario())
