from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = ROOT / "eval" / "cross_model_internal_40"


def test_blind_review_page_is_ready_and_self_contained() -> None:
    manifest = json.loads(
        (EXPERIMENT_ROOT / "blind_review_manifest_v1.json").read_text(encoding="utf-8")
    )
    html = (EXPERIMENT_ROOT / "blind_review.html").read_text(encoding="utf-8")

    assert manifest["status"] == "ready"
    assert manifest["review_type"] == "double_blind_pairwise"
    assert manifest["case_count"] == 40
    assert manifest["identities_hidden_until_reveal"] is True
    assert html.count('"review_ordinal":') == 40
    assert "候选 A" in html and "候选 B" in html
    assert "完成并揭盲" in html
    assert "localStorage" in html
    assert "导出审核结果" in html


def test_blind_review_hides_model_names_from_review_surface() -> None:
    html = (EXPERIMENT_ROOT / "blind_review.html").read_text(encoding="utf-8")
    review_markup = html.split('<section class="reveal-view"', 1)[0]

    assert "本地白未晞" not in review_markup
    assert "GPT-5.6 Sol" not in review_markup
    assert "A 更好" in review_markup
    assert "B 更好" in review_markup
