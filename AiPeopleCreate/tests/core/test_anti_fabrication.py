"""编造盲区修复验收（2026-08-08）。

- 前置：prompt 含具体禁令 + Few-shot 示范（回归锁：模板必须包含"正文事实纪律"与示例段）；
- 后置兜底：共同经历句式检查（非记忆类任务"上次/以前/那天"→ 拒绝；
  correction/vague 记忆话题放行；无 marker 通过）。
"""
from __future__ import annotations

from data_gen_v4.adapters.modes.reply import _check_shared_history


def _msgs(assistant: str, human: str = "h") -> list[dict[str, str]]:
    return [
        {"role": "human", "content": human},
        {"role": "assistant", "content": assistant},
    ]


def test_shared_history_rejected_outside_memory_tasks():
    # 玩家预设"上次" → 角色顺杆补造 → 拒绝（casual 等非记忆类）
    hits = _check_shared_history(
        _msgs("我上次吃那家麻辣烫辣得直喝水。", "你上次不是嫌那家太辣了吗？"),
        "reply_casual",
    )
    assert hits and any("上次" in h for h in hits)


def test_shared_history_markers():
    for marker in ("上次", "以前", "那天", "上回"):
        hits = _check_shared_history(_msgs(f"我{marker}也这样。"), "reply_romance")
        assert hits, f"marker {marker} 应被检出"


def test_memory_tasks_allowed():
    # correction/vague 记忆话题合法（记忆纠正/含糊回应谈记忆是池语义）
    assert _check_shared_history(_msgs("你以前不是这样的。"), "reply_correction") == []
    assert _check_shared_history(_msgs("上次你说要去看电影。"), "reply_vague") == []


def test_clean_dialogue_passes():
    assert _check_shared_history(_msgs("刚画完一张插画，顺手等你。"), "reply_romance") == []
    assert _check_shared_history(_msgs("别担心，我在这陪你。"), "reply_supportive") == []


def test_prompt_contains_foreground_discipline():
    """回归锁：prompt 前置纪律（具体禁令 + 示例）必须存在。"""
    text = open(
        "data_gen_v4/prompts/reply_merge.txt", encoding="utf-8"
    ).read()
    assert "正文事实纪律" in text
    assert "不得顺着补造细节" in text
    assert "【示例（正确示范" in text
    assert "{{CHARACTER_NAME}}" in text or "画完一张插画" in text
