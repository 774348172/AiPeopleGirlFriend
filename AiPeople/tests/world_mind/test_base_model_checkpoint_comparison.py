from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / "tools" / "build_base_model_checkpoint_comparison_report.py"
    spec = importlib.util.spec_from_file_location("checkpoint_comparison_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_comparison_contract_and_key_results() -> None:
    analysis = _load_module().build_analysis()

    assert analysis["comparison_contract"]["same_character_direct_cases"] == 61
    assert analysis["comparison_contract"][
        "checkpoint_298_adapter_equals_final_adapter"
    ]
    assert analysis["runs"]["base_q5"]["automatic_pass"] == 50
    assert analysis["runs"]["checkpoint_200_q5"]["automatic_pass"] == 29
    assert analysis["runs"]["checkpoint_298_f16"]["automatic_pass"] == 24
    assert analysis["runs"]["final_q5"]["automatic_pass"] == 30
    protocol = analysis["transitions"]["base_q5_to_checkpoint_200_q5_full"][
        "right_execution_failures_in_regressions"
    ]
    assert len(protocol["cases"]) == 14
    assert protocol["attempts"] == 19

    paired = analysis["paired_transition_adjudication"]["comparisons"]
    assert paired["base_q5_to_checkpoint_200_q5_direct"]["verdict_counts"] == {
        "right_better": 4,
        "left_better": 4,
        "mixed_or_evaluator_artifact": 1,
    }
    assert paired["checkpoint_298_f16_to_final_q5_direct"][
        "verdict_counts"
    ] == {
        "mixed_or_evaluator_artifact": 2,
        "left_better": 3,
        "right_better": 2,
    }


def test_report_keeps_automatic_and_human_results_separate() -> None:
    module = _load_module()
    report = module.render_report(module.build_analysis())

    assert "最终 Q5 的人工复核结果为 `46/89`" in report
    assert "不拿 `46/89` 与其他自动分数直接排名" in report
    assert "POST_REPLY_WORLD_MIND_RECONCILE" in report
    assert "MEMORY_CONSOLIDATE" not in report
