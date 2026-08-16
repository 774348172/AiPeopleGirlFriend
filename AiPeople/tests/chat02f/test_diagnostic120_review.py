from __future__ import annotations

from collections import Counter

from eval.chat02.reviewer.server import ReviewStore
from eval.diagnostic120.prepare_review import (
    CONTRACT_PATH,
    QUOTAS,
    TRAINED_MODEL_ID,
    build,
)
from eval.diagnostic120.prepare_revealed_review import (
    REVEALED_CONTRACT_PATH,
    build as build_revealed,
)


def test_review_package_has_40_stratified_and_side_balanced_units() -> None:
    contract = build()
    store = ReviewStore(CONTRACT_PATH)

    assert contract["expected_units"] == len(store.packets) == 40
    assert Counter(packet["scenario_class"] for packet in store.packets) == QUOTAS
    assert contract["selection"]["candidate_a_balance"] == {"v2500": 20, "base": 20}
    assert contract["rationale_required"] is False
    assert all("model_id" not in str(packet) for packet in store.packets)


def test_review_private_assignments_are_not_loaded_by_public_store() -> None:
    build()
    store = ReviewStore(CONTRACT_PATH)

    assert store.contract["privacy"]["private_assignment_access"] is False
    assert not hasattr(store, "assignments")
    assert TRAINED_MODEL_ID not in "".join(
        packet["candidate_a"]["output_id"] + packet["candidate_b"]["output_id"]
        for packet in store.packets
    )


def test_reveal_labels_only_after_all_ballots_are_submitted(tmp_path) -> None:
    build()
    build_revealed()
    store = ReviewStore(REVEALED_CONTRACT_PATH)
    actual_submissions = store.submission_root

    store.submission_root = tmp_path / "empty-submissions"
    blind = store.bootstrap("reviewer-main")
    assert blind["review_mode"] == "blind"
    assert all("model_label" not in packet["candidate_a"] for packet in blind["packets"])

    store.submission_root = actual_submissions
    revealed = store.bootstrap("reviewer-main")
    assert revealed["progress"] == {"submitted": 40, "total": 40}
    assert revealed["review_mode"] == "revealed_read_only"
    assert {
        packet[side]["model_label"]
        for packet in revealed["packets"]
        for side in ("candidate_a", "candidate_b")
    } == {"基座", "V2500"}
