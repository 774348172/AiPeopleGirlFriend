"""多角色接线：通用 style resolver（2026-08-08）。

覆盖（以白未晞为当前 P0 女主角基准；秦为历史角色，不做对照 diff）：
- 双 schema 归一化：秦式 bible（voice.register + player.name_slots，历史正典）
  与白未晞式 bible（voice.tone，无 player 段）→ StyleSpec 字段正确；
- 白未晞式：无崩溃、emoji/markdown=false、口癖空、无昵称规则段、无硬编码称呼；
- make_style_resolver：从 ProfilePackage.identity_sources 定位 bible；无 bible 时
  原样返回（与未注入 resolver 行为一致）；非 style: 引用原样返回。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from data_gen_v4.adapters.modes.style import (  # noqa: E402
    load_style_spec,
    make_style_resolver,
    render_style,
)
from gen_qin_v4 import ROOT as GEN_ROOT  # noqa: E402

QIN_BIBLE = GEN_ROOT / "历史与调研文档" / "历史角色" / "秦未晞" / "bible.yaml"
BAIWX_BIBLE = GEN_ROOT / "人物设定" / "白未晞" / "bible.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ───────────────────────── 归一化：秦式（历史正典，schema 机制验证） ─────────────────────────


def test_qin_schema_loads_all_fields():
    spec = load_style_spec(_load(QIN_BIBLE))
    assert spec.register == "北京式口语+活泼少女感，爱怼爱闹，但说真心话会突然变轻变安静"
    assert spec.emoji is False and spec.markdown is False
    assert "喂" in spec.vocabulary
    assert "哼，谁稀罕。" in spec.catchphrases
    assert "好的" in spec.forbid_assistant_speak
    assert spec.name_slots == {"formal": "浩然", "informal": "B哥"}


# ───────────────────────── 归一化：白未晞式（当前 P0 女主角） ─────────────────────────


def test_baiweixi_schema_loads_without_player_section():
    spec = load_style_spec(_load(BAIWX_BIBLE))
    # tone → register；sentence_style → 句长描述
    assert spec.register == "清冷、简短、自然，克制但不冷漠"
    assert "短句" in spec.sentence_length
    # 缺省字段：无口癖、无标点规则、emoji/markdown 默认 false
    assert spec.vocabulary == ""
    assert spec.catchphrases == []
    assert spec.punctuation == ""
    assert spec.emoji is False and spec.markdown is False
    # 无 player 段 → name_slots 为 None（不发明称呼）
    assert spec.name_slots is None
    # forbidden_tendencies → 严禁助手腔补充
    assert any("喵" in w for w in spec.forbid_assistant_speak)


def test_baiweixi_render_has_no_nickname_rule():
    text = render_style(load_style_spec(_load(BAIWX_BIBLE)), tics_enabled=True)
    assert "B哥" not in text and "浩然" not in text and "秦老" not in text
    assert "对玩家的默认称呼" not in text
    assert "第一人称" in text
    assert "emoji：禁止" in text and "markdown：禁止" in text


def test_baiweixi_empty_tics_falls_back_to_restrained():
    # 口癖清单为空：tics_enabled 分支无可注入 → 退化为克制版（T8 语义保留）
    spec = load_style_spec(_load(BAIWX_BIBLE))
    full = render_style(spec, tics_enabled=True)
    restrained = render_style(spec, tics_enabled=False)
    assert "口头禅：" not in full and "常用词：" not in full
    assert "口吻克制" in full
    assert full == restrained


# ───────────────────────── 通用 resolver 闭包 ─────────────────────────


def test_generic_resolver_passthrough_non_style_ref():
    profile = {"identity_sources": ["file:人物设定/白未晞/bible.yaml"]}
    generic = make_style_resolver(profile, GEN_ROOT)
    # 非 style: 引用（纯文本 style_contract）原样返回
    assert generic("第一人称、口语、自然。", tics_enabled=True) == "第一人称、口语、自然。"


def test_generic_resolver_missing_bible_passthrough():
    generic = make_style_resolver({"identity_sources": []}, GEN_ROOT)
    assert generic("style:whatever-v1", tics_enabled=True) == "style:whatever-v1"


def test_generic_resolver_unknown_bible_fails_clearly(tmp_path):
    profile = {"identity_sources": ["file:人物设定/不存在/bible.yaml"]}
    generic = make_style_resolver(profile, GEN_ROOT)
    with pytest.raises(FileNotFoundError):
        generic("style:x-v1", tics_enabled=True)


# ───────────────────────── 冒烟：白未晞 bible 全链路（当前 P0 基准） ─────────────────────────


def test_baiweixi_resolver_smoke():
    profile = {"identity_sources": ["file:人物设定/白未晞/bible.yaml"]}
    resolver = make_style_resolver(profile, GEN_ROOT)
    text = resolver("style:baiweixi-casual-v1", tics_enabled=True)
    assert isinstance(text, str) and len(text) > 30
    assert "第一人称" in text
    assert "B哥" not in text and "秦老" not in text
    # 白未晞专属禁倾向进入严禁助手腔（禁止"每句话加喵"等）
    assert "喵" in text or "卖萌" in text
    # 克制版同样可用
    restrained = resolver("style:baiweixi-casual-v1", tics_enabled=False)
    assert "口吻克制" in restrained
