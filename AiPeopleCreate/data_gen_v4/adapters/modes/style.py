"""通用 style contract resolver（2026-08-08 多角色接线，大块：core 去角色化延续）。

历史：gen_qin_v4.qin_style_resolver 直接读 人物设定/秦/bible.yaml 的固定字段
（voice.register/sentence_length/punctuation/... + player.name_slots），把
style:qinweixi-casual-v1 解析为口吻文本；新角色 bible 的 voice 段 schema 不同
（白未晞：tone/sentence_style/forbidden_tendencies，无 player 段），同一 resolver
会 KeyError。

本模块提供统一入口：
- load_style_spec(bible)：把不同 bible voice schema 归一化为 StyleSpec（双 schema）
- render_style(spec, tics_enabled)：合成为口吻文本（与 legacy 秦式输出逐字兼容）
- make_style_resolver(profile_pkg, root)：从 ProfilePackage.identity_sources 定位
  bible 文件 → 返回 (style_ref, tics_enabled) -> str 闭包（ReplyModeAdapter 注入点
  签名不变，reply.py 零改动）

归一化原则（与项目"不编造正典"一致）：
- 无 player.name_slots → 昵称规则段留空（不发明称呼，等人设作者补正典）
- 无 vocabulary/catchphrases（白未晞式无口癖设定）→ tics_enabled 分支自然退化为
  克制版（T8 语义保留：口癖清单为空时无可注入）
- emoji/markdown 缺失 → 默认 false（与 canon style_rules 的 emoji_allowed/
  markdown_allowed=false 一致）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml


@dataclass
class StyleSpec:
    """归一化后的口吻结构（秦式 / 白未晞式 bible voice 段的共同最小表示）。"""

    register: str = ""            # 口吻总述（秦 register / 白未晞 tone）
    sentence_length: str = ""     # 句长规则（秦 sentence_length / 白未晞 sentence_style）
    punctuation: str = ""         # 标点规则（白未晞缺省）
    emoji: bool = False
    markdown: bool = False
    vocabulary: str = ""          # 常用词（秦为字符串；白未晞无口癖 → 空）
    catchphrases: list[str] = field(default_factory=list)  # 口头禅（白未晞空）
    forbid_assistant_speak: list[str] = field(default_factory=list)  # 严禁助手腔/禁止倾向
    name_slots: dict[str, str] | None = None  # {"formal","informal"}；无称呼定义 → None


def load_style_spec(bible: dict[str, Any]) -> StyleSpec:
    """双 schema 归一化：识别 voice 段写法并映射为 StyleSpec。

    - 秦式（voice.register 存在）：字段直取，含 player.name_slots
    - 白未晞式（voice.tone 存在）：tone→register、sentence_style→句长、
      forbidden_tendencies→严禁助手腔补充；无口癖清单；emoji/markdown 默认 false
    - 两者皆无 → ValueError（显式失败，不静默降级）
    """
    voice = bible.get("voice") or {}
    spec = StyleSpec()
    if isinstance(voice.get("register"), str):
        # 秦式
        spec.register = voice["register"]
        spec.sentence_length = str(voice.get("sentence_length", "") or "")
        spec.punctuation = str(voice.get("punctuation", "") or "")
        spec.emoji = bool(voice.get("emoji", False))
        spec.markdown = bool(voice.get("markdown", False))
        spec.vocabulary = str(voice.get("vocabulary", "") or "")
        spec.catchphrases = list(voice.get("catchphrases", []) or [])
        spec.forbid_assistant_speak = list(voice.get("forbid_assistant_speak", []) or [])
    elif isinstance(voice.get("tone"), str):
        # 白未晞式
        spec.register = voice["tone"]
        spec.sentence_length = str(voice.get("sentence_style", "") or "")
        # punctuation 缺省 → 渲染时跳过该分句
        # emoji/markdown 缺省 → 默认 false（canon style_rules 同口径）
        spec.forbid_assistant_speak = list(voice.get("forbidden_tendencies", []) or [])
        # vocabulary/catchphrases 空：白未晞"不卖萌、无口癖"设定（T8 自然退化）
    else:
        raise ValueError(
            "bible voice 段既无 register 也无 tone，无法归一化 StyleSpec"
        )
    player = bible.get("player") or {}
    slots = (player or {}).get("name_slots") or {}
    formal = str(slots.get("formal", "") or "").strip()
    informal = str(slots.get("informal", "") or "").strip()
    # 2026-08-09：占位值（"玩家可配置"）视为无名字——名字由生成时 per-item
    # 注入（profile.anchor_contract.player_name / prompt 称呼段），bible 只声明
    # 结构与可配置性，不生成昵称规则段
    _PLACEHOLDER = ("玩家可配置", "可配置", "占位")
    if formal in _PLACEHOLDER:
        formal = ""
    if informal in _PLACEHOLDER:
        informal = ""
    if formal and informal:
        spec.name_slots = {"formal": formal, "informal": informal}
    return spec


def render_style(spec: StyleSpec, tics_enabled: bool = True) -> str:
    """把 StyleSpec 合成为口吻文本。

    与 legacy qin_style_resolver（gen_qin_v4 旧实现）对秦式输入的输出**逐字兼容**：
    有值分句按旧格式拼接；空值分句跳过（白未晞缺 punctuation 时不再输出空句）。
    """
    head = f"第一人称。{spec.register}。"
    if spec.sentence_length:
        head += f"句长：{spec.sentence_length}。"
    if spec.punctuation:
        head += f"标点：{spec.punctuation}。"
    head += f"emoji：{'允许' if spec.emoji else '禁止'}；"
    head += f"markdown：{'允许' if spec.markdown else '禁止'}；"
    if spec.forbid_assistant_speak:
        head += f"严禁助手腔：{'、'.join(spec.forbid_assistant_speak)} 等。"
    head += _render_nickname(spec.name_slots)
    if tics_enabled and (spec.vocabulary or spec.catchphrases):
        out = head
        if spec.vocabulary:
            out += f"常用词：{spec.vocabulary}。"
        if spec.catchphrases:
            out += f"口头禅：{'、'.join(spec.catchphrases).rstrip('。')}。"
        return out
    # 克制版（T8）：口癖清单为空（白未晞）或未命中注入比例时，同样要求自然说话
    return head + (
        "本轮要求口吻克制：少用语气词和口头禅，像平时一样自然说话，"
        "不要为了扮演角色而堆砌口头禅。"
    )


def _render_nickname(name_slots: dict[str, str] | None) -> str:
    """称呼规则段：有双槽位才生成；无定义留空（不发明设定）。"""
    if not name_slots:
        return ""
    formal = (name_slots.get("formal") or "").strip()
    informal = (name_slots.get("informal") or "").strip()
    if not formal or not informal:
        return ""
    return (
        f"对玩家的默认称呼：小名是{informal}（平常主要这么叫），"
        f"大名是{formal}（心情好的时候主要叫他的名字）。"
        f"这是设定不是强制——情境需要时也可以自由用其它称呼（连名带姓、喂、昵称等）。"
    )


def make_style_resolver(
    profile_pkg: dict[str, Any], root: Path
) -> Callable[[str, bool], str]:
    """从 ProfilePackage 构造通用 style resolver 闭包。

    bible 路径来自 profile_pkg.identity_sources 的 `file:人物设定/<id>/bible.yaml`
    引用（与 compiler 同一来源，单副本原则）；bible 缺失时返回"原样返回"闭包
    （与未注入 resolver 行为一致，不抛错——style_contract 可能本身就是纯文本）。
    """
    bible_rel: str | None = None
    for ref in profile_pkg.get("identity_sources") or []:
        if isinstance(ref, str) and ref.startswith("file:"):
            path = ref[len("file:"):]
            if path.lower().endswith((".yaml", ".yml")):
                bible_rel = path
                break
    if bible_rel is None:
        def _passthrough(style_ref: str, tics_enabled: bool = True) -> str:
            return style_ref
        return _passthrough

    _spec: StyleSpec | None = None

    def resolver(style_ref: str, tics_enabled: bool = True) -> str:
        nonlocal _spec
        if not isinstance(style_ref, str) or not style_ref.startswith("style:"):
            return style_ref
        if _spec is None:
            bible = yaml.safe_load(
                (root / bible_rel).read_text(encoding="utf-8")
            ) or {}
            _spec = load_style_spec(bible)
        return render_style(_spec, tics_enabled=tics_enabled)

    return resolver
