"""生成器测试路径注入与共享 fixture（2026-08-07 随生成器移入 AiPeopleCreate）。

- data_gen_v4 / data_gen 未注册进 pyproject（阶段 1 约束），通过本 conftest 把仓库根加入 sys.path。
- 提供跨目录共享 fixture（package_context / reply_item），供 tests/adapters 与 tests/stage3/stage4 复用。
- T2 锚一致性守卫（tests/test_reply_prompt.py）单独注入 AI 程序侧路径（F:\AiPeople），与本文件无关。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.core.fixtures import profile_alpha  # noqa: E402


@pytest.fixture()
def package_context():
    """编译上下文：profile 包 + 可注入事实（REPLY adapter 的 package_set）。"""
    return {
        "profile": profile_alpha(),
        "protocol": {},
        "recipe": {},
        "release": {},
        "snapshots": {},
        "facts": ["角色名叫阿尔法，住在河畔公寓。"],
    }


@pytest.fixture()
def reply_item():
    return {
        "plan_id": "plan-1:0000",
        "family_id": "fam:1",
        "mode": "REPLY",
        "task_type": "casual",
        "attempt_no": 1,
        "seed": 7,
        "input": {
            "scene": "晚上在客厅，她在打游戏你在加班",
            "topic": "今天吃什么",
            "player_view": "你们合租，今晚加班回来晚了",
            "turn_bounds": [4, 8],
        },
        "required_behaviors": [],
        "forbidden_behaviors": [],
        "source_event_ids": [],
        "support_spans": [],
    }
