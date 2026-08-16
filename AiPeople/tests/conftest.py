"""测试路径注入：AI 程序测试 import runtime/eval 需要项目根在 sys.path。

生成器（data_gen/data_gen_v4）及其测试位于 F:\ai-girlfriend\AiPeopleCreate。
本 conftest 只保留路径注入；生成器测试的共享 fixture（package_context/reply_item）
随生成器测试移至 AiPeopleCreate/tests/conftest.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
