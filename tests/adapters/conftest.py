"""tests/adapters 共享 fixtures：真实 source 文件、package context、模型响应脚本。"""
from __future__ import annotations

import json

import pytest

from tests.core.fixtures import profile_alpha

# ───────────────────────── 真实 source 文件 ─────────────────────────


@pytest.fixture()
def source_dir(tmp_path):
    (tmp_path / "identity.yaml").write_text(
        """
profile_id: fixture.alpha
identity:
  name: { id: alpha-name, value: "角色名叫阿尔法" }
  hometown: { id: alpha-town, value: "住在河畔公寓" }
""",
        encoding="utf-8",
    )
    (tmp_path / "canon.json").write_text(
        json.dumps(
            {
                "profile_id": "fixture.alpha",
                "facts": {
                    "cat": {"id": "cat-name", "value": "河畔公寓的猫叫豆包", "visibility": "profile_public"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "timeline.yaml").write_text(
        """
profile_id: fixture.alpha
events:
  - id: ev-move
    date: "5岁"
    summary: 五岁那年搬过三次家。
  - id: ev-cat
    date: "10岁"
    summary: 地下室里养过一只猫。
    visibility: profile_secret
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def markdown_canon(tmp_path):
    (tmp_path / "canon.md").write_text(
        """profile_id: fixture.beta

<!-- id: md-library -->
### 图书馆
贝塔是一名图书管理员。

<!-- id: md-closed -->
### 闭馆日
图书馆每周二闭馆。
""",
        encoding="utf-8",
    )
    return tmp_path


# 注：package_context / reply_item 已移至 tests/conftest.py（跨目录共享）


# ───────────────────────── 模型响应脚本 ─────────────────────────

# 合并输出（2026-08-05：semantic+style 单次调用）：骨架 + 对话一体
REPLY_OK_SCRIPT = [
    {
        "content": (
            '{"human_turns": ["你今晚怎么回来这么晚？"], '
            '"assistant_propositions": ["角色名叫阿尔法，住在河畔公寓。"], '
            '"assistant_tones": ["neutral"], '
            '"messages": ['
            '{"role": "human", "text": "你今晚怎么回来这么晚？"}, '
            '{"role": "assistant", "text": "加班啊。角色名叫阿尔法，住在河畔公寓，你说呢。"}, '
            '{"role": "human", "text": "那你吃饭了吗？"}, '
            '{"role": "assistant", "text": "点了外卖，还没到。"}]}'
        )
    },
]

RECALL_NOOP_SCRIPT = [
    {"content": '{"action": "NO_OP", "reason": "current_context_is_sufficient"}'}
]

RECALL_SEARCH_SCRIPT = [
    {
        "content": (
            '{"action": "search", '
            '"queries": [{"cue_types": ["topic", "time"], "terms": ["餐厅"], '
            '"time_hint": "上次提到时", "include_corrections": true}], '
            '"on_miss": "ask_clarification"}'
        )
    }
]

MEMORY_PROPOSE_SCRIPT = [
    {
        "content": (
            '{"action": "propose", "candidates": [{"claim_type": "preference", '
            '"subject": "user", "predicate": "prefers_drink", "object": "无糖豆浆", '
            '"status": "proposed", "supersedes_claim_id": null, '
            '"evidence": [{"event_id": "ev-1", "role": "support", '
            '"excerpt_start": 0, "excerpt_end": 8}]}]}'
        )
    }
]

MEMORY_NOOP_SCRIPT = [
    {"content": '{"action": "NO_OP", "reason": "insufficient_evidence", "candidates": []}'}
]
