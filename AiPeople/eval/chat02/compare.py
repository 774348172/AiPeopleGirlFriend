from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
STATUS_RANK = {"pass": 0, "invalid": 1, "fail": 2, "error": 3, "blocker": 4, "skipped": 5}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(v, ensure_ascii=False, separators=(",", ":")) + "\n" for v in values), encoding="utf-8", newline="\n")


def compare(base_dir: Path, trained_dir: Path, output_dir: Path) -> Path:
    if output_dir.exists():
        raise FileExistsError(output_dir)
    base_manifest, trained_manifest = _json(base_dir / "run_manifest.json"), _json(trained_dir / "run_manifest.json")
    base_agg, trained_agg = _json(base_dir / "aggregate.json"), _json(trained_dir / "aggregate.json")
    if base_manifest["exit_status"] != "passed" or trained_manifest["exit_status"] != "passed":
        raise ValueError("both source runs must have passed execution")
    for key in ("suite", "runtime", "generation"):
        left, right = base_manifest[key], trained_manifest[key]
        if key == "runtime":
            left = {k: v for k, v in left.items() if k not in {"profile_path"}}
            right = {k: v for k, v in right.items() if k not in {"profile_path"}}
        if left != right:
            raise ValueError(f"comparison contract differs at {key}")
    base = {x["attempt_id"]: x for x in _jsonl(base_dir / "auto_item_results.jsonl")}
    trained = {x["attempt_id"]: x for x in _jsonl(trained_dir / "auto_item_results.jsonl")}
    if base.keys() != trained.keys():
        raise ValueError("attempt IDs or execution order differ")
    deltas: list[dict[str, Any]] = []
    transitions: Counter[str] = Counter()
    category: dict[str, Counter[str]] = defaultdict(Counter)
    family: dict[str, Counter[str]] = defaultdict(Counter)
    cases = {}
    for suite_name in ("chat01_frozen_single_v1.jsonl", "chat01_frozen_multiturn_v1.jsonl"):
        for line in (ROOT / "eval/chat01/suites" / suite_name).read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            cases[case["case_id"]] = case
    for attempt_id in base:
        left, right = base[attempt_id], trained[attempt_id]
        transition = f"{left['status']}->{right['status']}"
        transitions[transition] += 1
        direction = "unchanged"
        if STATUS_RANK[right["status"]] < STATUS_RANK[left["status"]]:
            direction = "improved"
        elif STATUS_RANK[right["status"]] > STATUS_RANK[left["status"]]:
            direction = "regressed"
        meta = cases[left["case_id"]]
        category[meta["category"]][direction] += 1
        family[meta["scenario_family"]][direction] += 1
        deltas.append({
            "attempt_id": attempt_id, "case_id": left["case_id"],
            "category": meta["category"], "scenario_family": meta["scenario_family"],
            "seed": left["metrics"].get("seed"), "base_status": left["status"],
            "trained_status": right["status"], "direction": direction,
            "base_blocker_flags": left["blocker_flags"], "trained_blocker_flags": right["blocker_flags"],
            "base_output": left["output"], "trained_output": right["output"],
        })
    perf_delta = {}
    for metric in ("first_token_ms", "total_ms", "output_tokens"):
        perf_delta[metric] = {
            q: round(trained_agg["performance"][metric][q] - base_agg["performance"][metric][q], 3)
            for q in ("p50", "p95")
        }
    pack = lambda groups: {k: dict(sorted(v.items())) for k, v in sorted(groups.items())}
    summary = {
        "comparison_id": "chat02d-base-vs-qinweixi-v1",
        "base_run_id": base_manifest["run_id"], "trained_run_id": trained_manifest["run_id"],
        "suite_id": base_manifest["suite"]["suite_id"], "attempt_denominator": len(deltas),
        "contract_match": True, "status_transitions": dict(sorted(transitions.items())),
        "base_attempt_counts": base_agg["attempt_counts"], "trained_attempt_counts": trained_agg["attempt_counts"],
        "performance_delta_trained_minus_base": perf_delta,
        "category_direction": pack(category), "family_direction": pack(family),
        "automatic_conclusion_boundary": "Semantic pending-review items are not ranked as model quality wins or losses. Human review is required.",
    }
    output_dir.mkdir(parents=True)
    _write_json(output_dir / "comparison.json", summary)
    _write_jsonl(output_dir / "item_deltas.jsonl", deltas)
    report = f"""# CHAT-02D base vs Qin Weixi comparison

- Attempts: {len(deltas)}
- Contract match: true
- Base pass/fail/blocker: {base_agg['attempt_counts']['pass']} / {base_agg['attempt_counts']['fail']} / {base_agg['attempt_counts']['blocker']}
- Trained pass/fail/blocker: {trained_agg['attempt_counts']['pass']} / {trained_agg['attempt_counts']['fail']} / {trained_agg['attempt_counts']['blocker']}
- First-token P95 delta (trained-base): {perf_delta['first_token_ms']['p95']} ms
- Total P95 delta (trained-base): {perf_delta['total_ms']['p95']} ms

The 704 semantic-review attempts per model remain unresolved. This automatic report does not claim that either model is more human-like.
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8", newline="\n")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--trained", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare(args.base, args.trained, args.output)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "completed", "output": str(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
