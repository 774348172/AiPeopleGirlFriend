from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from eval.chat02.reviewer.server import ReviewError, ReviewServer, ReviewStore


STATIC_INDEX = Path(__file__).resolve().parents[2] / "eval" / "chat02" / "reviewer" / "static" / "index.html"
STATIC_APP = STATIC_INDEX.with_name("app.js")


def _complete_payload(
    store: ReviewStore,
    reviewer_id: str = "reviewer-test",
    unit_id: str | None = None,
) -> dict:
    unit_id = unit_id or store.packets[0]["unit_id"]
    return {
        "reviewer_id": reviewer_id,
        "unit_id": unit_id,
        "dimension_scores": {
            dimension: {"candidate_a": 4, "candidate_b": 3}
            for dimension in store.dimensions
        },
        "verdict": "a_better",
        "failure_reasons": {"candidate_a": [], "candidate_b": ["assistant_tone"]},
        "rationale": "A 直接承接了玩家当前表达，B 的服务式措辞更明显。",
    }


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    value = ReviewStore()
    value.submission_root = tmp_path / "submissions"
    return value


def test_contract_is_public_only_and_has_frozen_denominator(store: ReviewStore) -> None:
    assert store.contract["privacy"] == {
        "public_only": True,
        "private_assignment_access": False,
        "reveal_supported": False,
    }
    assert store.contract["source_units"] == 60
    assert len(store.packets) == 40
    assert [packet["unit_id"] for packet in store.packets] == store.contract["selected_unit_ids"]
    assert all("model_id" not in json.dumps(packet) for packet in store.packets)


def test_direct_file_open_redirects_to_local_reviewer_server() -> None:
    html = STATIC_INDEX.read_text(encoding="utf-8")
    assert 'location.protocol === "file:"' in html
    assert 'location.replace("http://127.0.0.1:18120/")' in html


def test_reviewer_query_restores_stable_identity_and_can_be_switched() -> None:
    html = STATIC_INDEX.read_text(encoding="utf-8")
    javascript = STATIC_APP.read_text(encoding="utf-8")
    assert 'id="switch-reviewer-button"' in html
    assert 'new URLSearchParams(window.location.search).get("reviewer_id")' in javascript
    assert 'byId("switch-reviewer-button").addEventListener' in javascript


def test_draft_roundtrip_and_resume(store: ReviewStore) -> None:
    payload = _complete_payload(store)
    payload["dimension_scores"]["relevance"]["candidate_b"] = None
    saved = store.save_draft(payload)
    assert saved["status"] == "saved"
    state = store.bootstrap(payload["reviewer_id"])
    assert payload["unit_id"] in state["drafts"]
    assert state["progress"] == {"submitted": 0, "total": 40}


def test_submit_is_complete_unrevealed_and_non_overwriting(store: ReviewStore) -> None:
    payload = _complete_payload(store)
    result = store.submit(payload)
    assert result["status"] == "submitted"
    state = store.bootstrap(payload["reviewer_id"])
    ballot = state["submitted"][payload["unit_id"]]
    blank = store.blank_by_id[payload["unit_id"]]
    assert ballot["candidate_a_output_id"] == blank["candidate_a_output_id"]
    assert ballot["candidate_b_output_id"] == blank["candidate_b_output_id"]
    assert ballot["revealed"] is False
    assert "reveal" not in ballot
    assert state["progress"]["submitted"] == 1
    with pytest.raises(ReviewError, match="already submitted"):
        store.submit({**payload, "verdict": "b_better"})
    with pytest.raises(ReviewError, match="locked"):
        store.save_draft(payload)


def test_rationale_is_optional_and_existing_text_is_preserved(store: ReviewStore) -> None:
    empty = _complete_payload(store, reviewer_id="empty-rationale")
    empty["rationale"] = ""
    store.submit(empty)
    assert store.bootstrap("empty-rationale")["submitted"][empty["unit_id"]]["rationale"] == ""

    explained = _complete_payload(store, reviewer_id="kept-rationale")
    expected = explained["rationale"]
    store.submit(explained)
    assert store.bootstrap("kept-rationale")["submitted"][explained["unit_id"]]["rationale"] == expected


def test_existing_first_twenty_ballots_resume_as_twenty_of_forty(store: ReviewStore) -> None:
    reviewer_id = "existing-reviewer"
    for packet in store.packets[:20]:
        store.submit(_complete_payload(store, reviewer_id, packet["unit_id"]))
    assert store.bootstrap(reviewer_id)["progress"] == {"submitted": 20, "total": 40}


def test_unselected_source_unit_is_rejected(store: ReviewStore) -> None:
    payload = _complete_payload(store)
    payload["unit_id"] = "human.pair.ordinary_low_drama.17"
    with pytest.raises(ReviewError, match="unknown unit_id"):
        store.submit(payload)


def test_submit_rejects_incomplete_and_both_unacceptable_without_reasons(store: ReviewStore) -> None:
    payload = _complete_payload(store)
    payload["dimension_scores"]["correctness"]["candidate_a"] = None
    with pytest.raises(ReviewError, match="missing score"):
        store.submit(payload)
    payload = _complete_payload(store)
    payload["verdict"] = "both_unacceptable"
    with pytest.raises(ReviewError, match="failure reasons"):
        store.submit(payload)


def test_export_contains_only_submitted_ballots(store: ReviewStore) -> None:
    payload = _complete_payload(store)
    store.save_draft(payload)
    assert store.export(payload["reviewer_id"]) == b""
    store.submit(payload)
    rows = store.export(payload["reviewer_id"]).decode("utf-8").splitlines()
    assert len(rows) == 1
    assert json.loads(rows[0])["revealed"] is False


def test_http_surface_has_no_private_route_and_rejects_foreign_origin(store: ReviewStore) -> None:
    server = ReviewServer(("127.0.0.1", 0), store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/health", timeout=3) as response:
            assert json.load(response)["status"] == "ok"
        for path in ("/private", "/api/private", "/../private/assignments.jsonl"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path, timeout=3)
            assert error.value.code == 404
        request = Request(
            base + "/api/draft",
            data=json.dumps(_complete_payload(store)).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "https://example.com"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
