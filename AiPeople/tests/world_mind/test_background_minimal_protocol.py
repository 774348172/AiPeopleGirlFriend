from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from runtime.world_mind import (
    B1_PROTOCOL,
    R2_PROTOCOL,
    ReconcileMindRequest,
    ReconcileSourceTurn,
    ReconcileWorldSnapshot,
    WorldMindStore,
    b1_payload,
    compile_b1,
    compile_r2,
    r2_payload,
)
from runtime.world_mind.model_failures import classify_model_failure
from tests.world_mind._helpers import INITIAL_GAME_TIME, session
from tests.world_mind.test_real_model_gateway import _requests
from tests.world_mind.test_sys10_memory_proposer import _request


def _reconcile_request() -> ReconcileMindRequest:
    prompt, heroine, turn_snapshot = _requests()
    snapshot = ReconcileWorldSnapshot(
        snapshot_id="background-snapshot",
        job_id="background-job",
        session=turn_snapshot.session,
        captured_game_time=turn_snapshot.captured_game_time,
        live_world_version=turn_snapshot.live_world_version,
        mind_state_version=heroine.version,
        world_state=turn_snapshot.world_state,
        protagonist=turn_snapshot.protagonist,
        scene=turn_snapshot.scene,
        heroine_runtime=heroine,
        source_event_ids=("event-user", "event-reply"),
        selected_memory_frame=turn_snapshot.selected_memory_frame,
    )
    return ReconcileMindRequest(
        mode="POST_REPLY_WORLD_MIND_RECONCILE",
        prompt=prompt,
        snapshot=snapshot,
        elapsed_game_seconds=0.0,
        source_turn=ReconcileSourceTurn(
            request_id="source-turn",
            user_event_id="event-user",
            assistant_event_id="event-reply",
            protagonist_utterance="你是不是有点担心我？",
            heroine_reply="吃慢一点。",
        ),
    )


def test_b1_payload_is_bounded_and_excludes_runtime_metadata() -> None:
    payload = b1_payload(_reconcile_request())
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert payload["p"] == B1_PROTOCOL
    assert set(payload) <= {"p", "q", "s", "w", "x", "d", "u", "m", "f"}
    assert all(
        forbidden not in encoded
        for forbidden in (
            "save_001",
            "songjiangfu",
            "background-snapshot",
            "background-job",
            "mind_state_version",
            "live_world_version",
        )
    )
    assert len(encoded.encode("utf-8")) <= 1800


def test_compile_b1_restores_runtime_owned_identity_and_evidence() -> None:
    request = _reconcile_request()
    result = compile_b1(
        {"c": [[2, "克制的关心", [2]]], "m": ["男主回应了她的关心"]},
        request,
    )
    assert result.operation == "update"
    assert result.parent_mind_state_version == request.snapshot.mind_state_version
    assert result.snapshot_id == request.snapshot.snapshot_id
    assert result.latest_world_version == request.snapshot.live_world_version
    assert result.patch is not None
    assert result.patch.living_mind.emotion == "克制的关心"
    assert result.patch.evidence_refs == ("event-reply",)
    assert result.memory_candidates == ("男主回应了她的关心",)


def test_r2_requires_only_core_memory_semantics() -> None:
    request = _request()
    payload = r2_payload(request)
    assert payload["p"] == R2_PROTOCOL
    draft = compile_r2(
        {"m": [{"k": 2, "s": "男主喜欢雨夜", "e": [0]}]},
        request,
    )[0]
    assert draft.kind == "preference_boundary"
    assert draft.subject.subject_type == "player"
    assert draft.epistemic.modality == "asserted"
    assert draft.temporal.relation == "atemporal"
    assert draft.evidence_quotes[0].event_id == request.source_events[0].event_id


def test_reconcile_retry_is_persistently_delayed(tmp_path) -> None:
    now = [datetime(2026, 8, 14, tzinfo=timezone.utc)]
    store = WorldMindStore.open(
        tmp_path / "background-backoff.sqlite3",
        recorded_clock=lambda: now[0],
    )
    try:
        job = store.enqueue_periodic_reconcile(
            session(),
            INITIAL_GAME_TIME,
            interval_seconds=300.0,
        )
        claimed = store.claim_next_reconcile_job(session())
        assert claimed is not None
        store.fail_reconcile_job(
            claimed,
            "model_timeout",
            retryable=True,
            retry_delay_seconds=10.0,
        )
        assert store.claim_next_reconcile_job(session()) is None
        now[0] += timedelta(seconds=11)
        retried = store.claim_next_reconcile_job(session())
        assert retried is not None
        assert retried.job_id == job.job_id
        assert retried.attempt == 2
    finally:
        store.close()


def test_http_400_is_a_deterministic_background_request_failure() -> None:
    assert classify_model_failure(
        RuntimeError("HTTP 400: invalid structured output schema")
    ) == "model_request_invalid"
