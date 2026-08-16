from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace

import pytest

from runtime._memory_propose import (
    MEMORY_PROPOSE_FAILURE_CODES,
    MEMORY_PROPOSE_LIMITS,
    ExistingMemoryContext,
    MemoryProposeError,
    MemoryProposeEvent,
    MemoryProposeFailure,
    canonical_memory_propose_request_json,
    memory_propose_failure,
    memory_propose_request_from_dict,
    memory_propose_request_to_dict,
)

from ._helpers import request, request_dict


def test_request_round_trip_is_immutable_and_canonical() -> None:
    source = request_dict()
    value = memory_propose_request_from_dict(source)
    assert isinstance(value.events[0], MemoryProposeEvent)
    assert memory_propose_request_to_dict(value) == source
    assert canonical_memory_propose_request_json(value) == canonical_memory_propose_request_json(
        memory_propose_request_from_dict(dict(reversed(tuple(source.items()))))
    )
    with pytest.raises(FrozenInstanceError):
        value.mode = "REPLY"
    with pytest.raises(AttributeError):
        value.events.append(value.events[0])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["events"][0].update(commit_state="cancelled"),
        lambda value: value.update(from_sequence_no=9),
        lambda value: value["events"].reverse(),
        lambda value: value["events"][1].update(event_id="event-user-10"),
        lambda value: value["events"][1].update(conversation_id="conversation-other"),
        lambda value: value.update(max_proposals=7),
    ],
)
def test_request_cross_field_boundaries_reject_invalid_windows(mutate) -> None:
    source = deepcopy(request_dict())
    mutate(source)
    with pytest.raises(MemoryProposeError):
        memory_propose_request_from_dict(source)


def test_request_rejects_unknown_fields_and_uncommitted_event_types() -> None:
    source = deepcopy(request_dict())
    source["status"] = "active"
    with pytest.raises(MemoryProposeError) as caught:
        memory_propose_request_from_dict(source)
    assert caught.value.code == "memory_propose_schema_invalid"

    source = deepcopy(request_dict())
    source["events"][1]["event_type"] = "generation_cancelled"
    with pytest.raises(MemoryProposeError) as caught:
        memory_propose_request_from_dict(source)
    assert caught.value.code == "memory_propose_schema_invalid"


def test_existing_memory_must_share_conversation_and_have_valid_subject() -> None:
    source = deepcopy(request_dict())
    source["existing_memories"] = [
        {
            "memory_id": "memory-1",
            "conversation_id": "conversation-other",
            "kind": "player_fact",
            "statement": "玩家住在杭州",
            "subject": {"type": "player", "entity_id": None, "display_name": None},
            "status": "active",
        }
    ]
    with pytest.raises(MemoryProposeError) as caught:
        memory_propose_request_from_dict(source)
    assert caught.value.code == "memory_propose_contract_invalid"

    source["existing_memories"][0]["conversation_id"] = "conversation-fixture"
    source["existing_memories"][0]["subject"]["type"] = "character"
    with pytest.raises(MemoryProposeError) as caught:
        memory_propose_request_from_dict(source)
    assert caught.value.code == "memory_propose_contract_invalid"

def test_failure_policy_has_exact_retryability_and_no_raw_output_field() -> None:
    for code in MEMORY_PROPOSE_FAILURE_CODES:
        failure = memory_propose_failure("proposal-run-1", code)
        assert isinstance(failure, MemoryProposeFailure)
        assert failure.retryable is (code in {"memory_propose_timeout", "memory_propose_model_unavailable"})
        assert not hasattr(failure, "raw_output")
        assert not hasattr(failure, "prompt")

    with pytest.raises(MemoryProposeError):
        MemoryProposeFailure("proposal-run-1", "memory_propose_timeout", False)


def test_frozen_limits_keep_mode_short_and_bounded() -> None:
    assert MEMORY_PROPOSE_LIMITS == {
        "events_min": 1,
        "events_max": 32,
        "event_text_chars_max": 4000,
        "existing_memories_max": 32,
        "proposals_max": 8,
        "output_bytes_max": 32768,
    }


def test_direct_replacement_cannot_bypass_frozen_max_proposals() -> None:
    with pytest.raises(MemoryProposeError):
        replace(request(), max_proposals=99)
