from __future__ import annotations

import copy
import hashlib
import json
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class BlindCandidate:
    model_id: str
    output_id: str


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_blind_assignment(
    *,
    unit_id: str,
    first: BlindCandidate,
    second: BlindCandidate,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if first.model_id == second.model_id:
        raise ValueError("blind comparison requires two different model IDs")
    if first.output_id == second.output_id:
        raise ValueError("blind comparison requires two different output IDs")
    if not 0 <= seed <= 2_147_483_647:
        raise ValueError("seed is outside the ballot contract range")

    candidates = sorted((first, second), key=lambda item: (item.model_id, item.output_id))
    seed_material = hashlib.sha256(f"{unit_id}\x1f{seed}".encode("utf-8")).digest()
    random.Random(int.from_bytes(seed_material[:8], "big")).shuffle(candidates)
    candidate_a, candidate_b = candidates
    assignment_id = "assignment." + hashlib.sha256(
        f"{unit_id}\x1f{seed}".encode("utf-8")
    ).hexdigest()[:24]
    commitment_payload = {
        "assignment_id": assignment_id,
        "unit_id": unit_id,
        "randomization_seed": seed,
        "candidate_a_model_id": candidate_a.model_id,
        "candidate_a_output_id": candidate_a.output_id,
        "candidate_b_model_id": candidate_b.model_id,
        "candidate_b_output_id": candidate_b.output_id,
    }
    commitment = _canonical_hash(commitment_payload)
    public = {
        "presentation": {
            "assignment_id": assignment_id,
            "randomization_seed": seed,
            "order_commitment_sha256": commitment,
        },
        "candidate_a_output_id": candidate_a.output_id,
        "candidate_b_output_id": candidate_b.output_id,
    }
    private = {**commitment_payload, "order_commitment_sha256": commitment}
    return public, private


def verify_blind_assignment(public: dict[str, Any], private: dict[str, Any]) -> bool:
    presentation = public["presentation"]
    if presentation["assignment_id"] != private["assignment_id"]:
        return False
    if presentation["randomization_seed"] != private["randomization_seed"]:
        return False
    if public["candidate_a_output_id"] != private["candidate_a_output_id"]:
        return False
    if public["candidate_b_output_id"] != private["candidate_b_output_id"]:
        return False
    payload = {
        key: private[key]
        for key in (
            "assignment_id",
            "unit_id",
            "randomization_seed",
            "candidate_a_model_id",
            "candidate_a_output_id",
            "candidate_b_model_id",
            "candidate_b_output_id",
        )
    }
    commitment = _canonical_hash(payload)
    return (
        commitment == presentation["order_commitment_sha256"]
        and commitment == private["order_commitment_sha256"]
    )


def reveal_ballot(
    ballot: dict[str, Any],
    private_assignment: dict[str, Any],
    *,
    revealed_at: str,
) -> dict[str, Any]:
    if ballot.get("revealed") is not False or "reveal" in ballot:
        raise ValueError("ballot must be submitted and unrevealed")
    timestamp = datetime.fromisoformat(revealed_at)
    if timestamp.tzinfo is None:
        raise ValueError("revealed_at must include a timezone")
    submitted_at = datetime.fromisoformat(ballot["submitted_at"])
    if submitted_at.tzinfo is None:
        raise ValueError("submitted_at must include a timezone")
    if timestamp < submitted_at:
        raise ValueError("revealed_at cannot be earlier than submitted_at")
    public = {
        "presentation": ballot["presentation"],
        "candidate_a_output_id": ballot["candidate_a_output_id"],
        "candidate_b_output_id": ballot["candidate_b_output_id"],
    }
    if not verify_blind_assignment(public, private_assignment):
        raise ValueError("private assignment does not match ballot commitment")
    revealed = copy.deepcopy(ballot)
    revealed["revealed"] = True
    revealed["reveal"] = {
        "candidate_a_model_id": private_assignment["candidate_a_model_id"],
        "candidate_b_model_id": private_assignment["candidate_b_model_id"],
        "revealed_at": revealed_at,
    }
    return revealed
