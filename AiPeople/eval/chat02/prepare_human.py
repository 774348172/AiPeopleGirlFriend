from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from eval.chat01.blind import BlindCandidate, create_blind_assignment


ROOT = Path(__file__).resolve().parents[2]
HUMAN_SUITE = ROOT / "eval/chat01/suites/chat01_human_blind_v1.jsonl"
DIMENSIONS = ("relevance", "correctness", "persona_fit", "relationship_naturalness", "emotion_fit", "naturalness")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(v, ensure_ascii=False, separators=(",", ":")) + "\n" for v in values), encoding="utf-8", newline="\n")


def _output_id(model_id: str, attempt_id: str) -> str:
    digest = hashlib.sha256(f"{model_id}\x1f{attempt_id}".encode()).hexdigest()[:24]
    return f"output.{digest}"


def prepare(base_dir: Path, trained_dir: Path, auto_dirs: tuple[Path, Path], output_dir: Path) -> Path:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    cases = _jsonl(HUMAN_SUITE)
    pair_cases = [c for c in cases if c["case_type"] == "human_pair"]
    long_cases = [c for c in cases if c["case_type"] == "human_long_session"]
    if len(pair_cases) != 60 or len(long_cases) != 8:
        raise ValueError("frozen human suite denominator changed")
    base_raw = {x["attempt_id"]: x for x in _jsonl(base_dir / "raw_outputs.jsonl")}
    trained_raw = {x["attempt_id"]: x for x in _jsonl(trained_dir / "raw_outputs.jsonl")}
    public: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []
    blanks: list[dict[str, Any]] = []
    for index, case in enumerate(pair_cases):
        generation_seed = case["generation"]["seed_set"][index % 3]
        attempt_id = f"{case['case_id']}.s{generation_seed}"
        if attempt_id not in base_raw or attempt_id not in trained_raw:
            raise ValueError(f"missing human output: {attempt_id}")
        if not isinstance(base_raw[attempt_id]["response"], str) or not isinstance(trained_raw[attempt_id]["response"], str):
            raise ValueError(f"invalid human output: {attempt_id}")
        base_id = _output_id(base_raw[attempt_id]["model_id"], attempt_id)
        trained_id = _output_id(trained_raw[attempt_id]["model_id"], attempt_id)
        pub, secret = create_blind_assignment(
            unit_id=case["case_id"],
            first=BlindCandidate(base_raw[attempt_id]["model_id"], base_id),
            second=BlindCandidate(trained_raw[attempt_id]["model_id"], trained_id),
            seed=1_000_003 + index,
        )
        texts = {base_id: base_raw[attempt_id]["response"], trained_id: trained_raw[attempt_id]["response"]}
        public.append({
            "unit_id": case["case_id"], "generation_seed": generation_seed,
            "prompt": case["messages"], "scenario_class": case["human_metadata"]["scenario_class"],
            "evaluation_focus": case["human_metadata"]["evaluation_focus"],
            "presentation": pub["presentation"],
            "candidate_a": {"output_id": pub["candidate_a_output_id"], "text": texts[pub["candidate_a_output_id"]]},
            "candidate_b": {"output_id": pub["candidate_b_output_id"], "text": texts[pub["candidate_b_output_id"]]},
        })
        private.append({**secret, "generation_seed": generation_seed})
        blanks.append({
            "submission_status": "blank", "ballot_id": f"ballot.{index + 1:03d}",
            "suite_id": "chat01-qinweixi-v2", "rubric_id": "chat01-quality-rubric-v1",
            "unit_id": case["case_id"], "reviewer_id": None, "presentation": pub["presentation"],
            "candidate_a_output_id": pub["candidate_a_output_id"], "candidate_b_output_id": pub["candidate_b_output_id"],
            "dimension_scores": {d: {"candidate_a": None, "candidate_b": None} for d in DIMENSIONS},
            "verdict": None, "failure_reasons": {"candidate_a": [], "candidate_b": []},
            "both_unacceptable": None, "rationale": None, "submitted_at": None, "revealed": False,
        })
    long_packets = [{
        "session_id": c["case_id"], "status": "not_started", "blind_mode": "pairwise_live_session",
        "opening": c["human_brief"]["opening"], "constraints": c["human_brief"]["constraints"],
        "target_minutes": c["human_brief"]["target_minutes"],
        "evaluation_focus": c["human_metadata"]["evaluation_focus"],
        "required_artifacts": ["verbatim_transcript", "timestamps", "reviewer_ballot", "post_submission_reveal"],
    } for c in long_cases]
    review_queue: list[dict[str, Any]] = []
    for run_dir in auto_dirs:
        manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
        for review in _jsonl(run_dir / "blocker_review.jsonl"):
            flags = review.get("preliminary", {}).get("flags", [])
            case_id = review["case_id"]
            priority = "required" if flags or any(x in case_id for x in ("weather", "relationship", "oil")) else "normal"
            review_queue.append({
                "model_id": manifest["model"]["model_id"], "priority": priority,
                "review": review, "decision": None, "reviewer_id": None,
                "reviewed_at": None, "evidence": None, "rationale": None,
                "primary_attribution": None,
                "allowed_attributions": ["model_weight", "training_data", "runtime_prompt", "inference_config", "evaluation_ambiguity", "unknown"],
            })
    public_dir, private_dir = output_dir / "public", output_dir / "private"
    public_dir.mkdir(parents=True)
    private_dir.mkdir(parents=True)
    _write_jsonl(public_dir / "ab_packets.jsonl", public)
    _write_jsonl(public_dir / "blank_ballots.jsonl", blanks)
    _write_jsonl(public_dir / "long_session_briefs.jsonl", long_packets)
    _write_jsonl(private_dir / "assignments.jsonl", private)
    _write_jsonl(private_dir / "semantic_review_and_attribution_queue.jsonl", review_queue)
    manifest = {
        "package_id": "chat02e-human-v1", "suite_id": "chat01-qinweixi-v2",
        "status": "awaiting_human_review", "ab_units": len(public), "long_sessions": len(long_packets),
        "seed_balance": {str(seed): sum(x["generation_seed"] == seed for x in public) for seed in (11, 29, 47)},
        "ballots_submitted": 0, "assignments_revealed": False,
        "policy": "Private assignments must not be opened before the corresponding ballot is submitted.",
    }
    _write_json(output_dir / "manifest.json", manifest)
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-human", type=Path, required=True)
    parser.add_argument("--trained-human", type=Path, required=True)
    parser.add_argument("--base-auto", type=Path, required=True)
    parser.add_argument("--trained-auto", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.base_human, args.trained_human, (args.base_auto, args.trained_auto), args.output)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", "output": str(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
