from __future__ import annotations

from dataclasses import replace

import pytest

from runtime.world_mind import ShortProtocolError, compile_r1
from tests.world_mind.test_sys10_memory_proposer import _request


def test_compile_r1_basic_preference_uses_full_committed_event() -> None:
    request = _request()
    draft = compile_r1(
        {"m": [[2, "男主喜欢雨夜", 1, 1, 0, [0]]]},
        request,
    )[0]
    assert draft.kind == "preference_boundary"
    assert draft.subject.subject_type == "player"
    assert draft.temporal.relation == "atemporal"
    assert draft.evidence_quotes[0].event_id == request.source_events[0].event_id
    assert draft.evidence_quotes[0].quote == request.source_events[0].text
    assert draft.semantic_reason == "R1 semantic memory proposal"
    assert draft.confidence == 1.0


def test_compile_r1_preserves_ambiguous_future_expression() -> None:
    draft = compile_r1(
        {"m": [[6, "男主过几天准备去咖啡厅", 1, 1, 3, [0], "过几天"]]},
        _request(),
    )[0]
    assert draft.temporal.relation == "future"
    assert draft.temporal.resolution == "ambiguous"
    assert draft.temporal.source_text == "过几天"
    assert draft.temporal.start_at is None


def test_compile_r1_rejects_event_and_relation_alias_escape() -> None:
    request = _request()
    with pytest.raises(ShortProtocolError):
        compile_r1({"m": [[1, "虚构", 1, 1, 0, [99]]]}, request)
    with pytest.raises(ShortProtocolError):
        compile_r1(
            {"m": [[1, "虚构", 1, 1, 0, [0], None, [3, 99]]]},
            request,
        )


def test_compile_r1_empty_result_is_valid() -> None:
    assert compile_r1({"m": []}, _request()) == ()
