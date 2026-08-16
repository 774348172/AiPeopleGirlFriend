from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = (
    ROOT
    / "eval"
    / "baiweixi_quality"
    / "runs"
    / "baiweixi-quality-formal-20260811"
)


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def main(run_dir: Path) -> dict[str, object]:
    source_path = run_dir / "manual_review_queue.jsonl"
    adjudication_path = run_dir / "manual_adjudication_v1.json"
    output_path = run_dir / "manual_review_queue_adjudicated.jsonl"
    source = _load_jsonl(source_path)
    adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
    by_case = {
        str(item["case_id"]): item for item in adjudication["adjudications"]
    }
    source_ids = [str(item["case_id"]) for item in source]
    if len(by_case) != len(adjudication["adjudications"]):
        raise RuntimeError("manual adjudication contains duplicate case IDs")
    if set(source_ids) != set(by_case):
        raise RuntimeError("manual adjudication does not exactly cover the review queue")

    counts: Counter[str] = Counter()
    with output_path.open("w", encoding="utf-8") as stream:
        for item in source:
            case_id = str(item["case_id"])
            reviewed = by_case[case_id]
            value = dict(item)
            value["review_status"] = "adjudicated"
            value["manual_review"] = {
                "review_type": adjudication["review_type"],
                "reviewer": adjudication["reviewer"],
                "reviewed_at": adjudication["reviewed_at"],
                "final_decision": reviewed["final_decision"],
                "final_attribution": reviewed["final_attribution"],
                "reason": reviewed["reason"],
            }
            counts[str(reviewed["final_decision"])] += 1
            stream.write(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            )

    summary = {
        "run_id": adjudication["run_id"],
        "reviewed_cases": len(source),
        "decisions": dict(counts),
        "character_failures": sum(
            item["final_attribution"] == "character_failure"
            for item in adjudication["adjudications"]
        ),
        "joint_failures": sum(
            item["final_attribution"] == "joint_or_ambiguous"
            for item in adjudication["adjudications"]
        ),
        "output_path": str(output_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply the reviewed Bai Weixi adjudication overlay to the failure queue."
    )
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    return parser


if __name__ == "__main__":
    main(_parser().parse_args().run_dir.resolve())
