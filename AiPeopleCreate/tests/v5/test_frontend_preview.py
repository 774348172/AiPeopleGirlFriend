from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import build_game_reply_preview as preview


def test_all_preview_inputs_equal_production_with_final_only_supervision():
    _, _, anchor = preview._load_baiweixi_render_context()
    samples = list(preview.candidates())
    assert len(samples) == 24
    for scenario, target, kind in samples:
        row = preview.export_row(scenario, target, anchor)
        assert row["supervised_message_indexes"] == [len(row["messages"]) - 1]
        assert row["messages"][-1]["content"] == target["reply"]
        system = row["messages"][0]["content"]
        assert "第30天 18:30" in system
        assert not any(word in system for word in ("JUDGE_TURN", "snapshot_id", "oracle_view"))
        if kind == "第三方归属":
            assert "说话人：" in system
            assert all(m["role"] != "assistant" for m in row["messages"][:-1])
        if kind == "玩家报告待确认":
            assert "待确认" in system
            assert "无已登记的当前权威事实" in system


def test_export_rejects_production_input_divergence(monkeypatch):
    scenario, target, _ = next(preview.candidates())
    monkeypatch.setattr(preview, "production_messages", lambda *_: [])
    with pytest.raises(ValueError, match="Production input differs"):
        preview.export_row(scenario, target, "你是白未晞。")


def test_build_rejects_empty_review_before_creating_output(tmp_path):
    import json
    source = tmp_path / "empty.json"
    source.write_text(json.dumps({"cases": [{"human_review": {
        "base": "", "old_adapter": "", "new_adapter": ""}}] * 12}), encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete human review"):
        preview.build(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()
