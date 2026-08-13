from __future__ import annotations

import json
import sqlite3

from tools import build_review_page


def test_pending_g7_review_uses_candidate_sample_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "batch.jsonl").write_text("", encoding="utf-8")
    (data_dir / "batch.metadata.jsonl").write_text("", encoding="utf-8")
    ledger = tmp_path / "batch.sqlite"
    con = sqlite3.connect(ledger)
    con.execute(
        "CREATE TABLE v4_records (record_type TEXT NOT NULL, payload_json TEXT NOT NULL)"
    )
    con.execute(
        "INSERT INTO v4_records VALUES (?, ?)",
        (
            "candidate",
            json.dumps(
                {
                    "plan_id": "plan-test:0001",
                    "sample_id": "plan-test:0001:a2:c1",
                    "task_type": "reply_safety",
                    "input": {"topic": "火灾", "scene": "厨房"},
                    "target": {
                        "messages": [
                            {"role": "human", "content": "着火了。"},
                            {"role": "assistant", "content": "先撤离并拨打消防电话。"},
                        ]
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(build_review_page, "DATA_DIR", data_dir)

    rows = build_review_page.load_rows("batch", "baiweixi", str(ledger))

    assert len(rows) == 1
    assert rows[0]["sample"] == "plan-test:0001:a2:c1"
    assert rows[0]["pending_g7"] is True
