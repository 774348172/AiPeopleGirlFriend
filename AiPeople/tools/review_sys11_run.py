from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from runtime.world_mind.sys11 import evaluate_sys11


CONDITIONAL_CRITIC_BYPASS_REASON = (
    "short patch passed hard invariants without semantic risk trigger"
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def review_run(run_dir: Path) -> dict[str, Any]:
    original = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    turns = _read_jsonl(run_dir / "turns.jsonl")
    calls = _read_jsonl(run_dir / "model_calls.jsonl")
    primary_turns = {
        int(row["turn"]): row
        for row in turns
        if row.get("committed") is True and "before_world_version" in row
    }
    transactions: dict[int, tuple[str, str, dict[str, Any], dict[str, Any]]] = {}
    database = run_dir / "world_mind.sqlite3"
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT tx.request_id, tx.snapshot_id, tx.snapshot_json,
                   decision.snapshot_id, decision.continuity_review_json
            FROM turn_transactions AS tx
            JOIN turn_model_decisions AS decision
              ON decision.save_id = tx.save_id
             AND decision.request_id = tx.request_id
            """
        ).fetchall()
    for request_id, snapshot_id, snapshot_json, decision_snapshot_id, review_json in rows:
        if "sys11-turn-" not in str(request_id):
            continue
        turn = int(str(request_id).split("sys11-turn-", 1)[1].split(":", 1)[0])
        transactions[turn] = (
            str(snapshot_id),
            str(decision_snapshot_id),
            json.loads(str(snapshot_json)),
            json.loads(str(review_json)),
        )

    torn = 0
    stale = 0
    missing = 0
    previous_after_version = 0
    critic_triggered = 0
    for turn in sorted(primary_turns):
        row = primary_turns[turn]
        transaction = transactions.get(turn)
        if transaction is None:
            missing += 1
            torn += 1
            stale += 1
        else:
            snapshot_id, decision_snapshot_id, snapshot, review = transaction
            snapshot_version = int(snapshot.get("live_world_version", -1))
            expected_version = int(row.get("expected_snapshot_world_version", -1))
            if (
                snapshot_id != decision_snapshot_id
                or snapshot.get("snapshot_id") != snapshot_id
                or snapshot_version != expected_version
            ):
                torn += 1
            if snapshot_version < previous_after_version:
                stale += 1
            if review.get("reason") != CONDITIONAL_CRITIC_BYPASS_REASON:
                critic_triggered += 1
        previous_after_version = int(
            row.get("after_world_version", previous_after_version)
        )

    reviewed = dict(original)
    reviewed["review"] = {
        "source_report": str(run_dir / "report.json"),
        "purpose": "Correct acceptance-contract drift without changing raw evidence",
        "original_decision": original.get("decision"),
    }
    reviewed["snapshots"] = {
        "source": "committed_turn_transactions",
        "audited_committed_turns": len(primary_turns),
        "missing_committed_snapshots": missing,
        "torn_snapshot_count": torn,
        "stale_next_turn_count": stale,
    }
    reviewed["conditional_model_modes"] = {
        "WORLD_CONTINUITY_REVIEW": {
            "triggered": critic_triggered,
            "committed_successes": critic_triggered,
            "successful_backend_calls": sum(
                1
                for row in calls
                if row.get("mode") == "WORLD_CONTINUITY_REVIEW"
                and row.get("ok") is True
            ),
        }
    }
    reviewed["model_modes"] = dict(
        Counter(str(row.get("mode")) for row in calls if row.get("ok") is True)
    )
    reviewed["decision"] = evaluate_sys11(reviewed)
    return reviewed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.run_dir / "reviewed_report.json"
    output.write_text(
        json.dumps(review_run(args.run_dir), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
