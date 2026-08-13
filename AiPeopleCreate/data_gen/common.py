"""data_gen 共享工具：配置/人设加载、OpenAI 兼容调用、JSON 解析、正典块渲染。

设计说明
- 人设=正典种子。bible.yaml 给人格与口吻，timeline.yaml 给人生事件，
  canon.json 给可校验事实。三者加载后封装为 Persona。
- 模型走 OpenAI 兼容接口，可指向任意供应商/本地。无 key 时退化为 mock，
  便于不联网也能跑通流水线（但 mock 不验证"命门"，仅验证管线）。
- prompt 模板用 {{TOKEN}} 占位符 + str.replace，避免 .format 的花括号转义之苦。
"""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


# ───────────────────────── 配置 ─────────────────────────

def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    """加载 config.yaml，环境变量对模型设置有更高优先级。"""
    load_dotenv(ROOT / ".env")
    with open(ROOT / path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # 环境变量覆盖
    if os.getenv("OPENAI_BASE_URL"):
        cfg["model"]["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.getenv("STRONG_MODEL"):
        cfg["model"]["model"] = os.environ["STRONG_MODEL"]
    return cfg


# ───────────────────────── 人设 ─────────────────────────

@dataclass
class Persona:
    bible: dict[str, Any]
    timeline: list[dict[str, Any]]
    canon: dict[str, Any]

    @property
    def name(self) -> str:
        return self.bible["identity"]["name"]

    @property
    def birth_date(self) -> str:
        return self.bible["identity"]["birth_date"]

    @property
    def relationships(self) -> list[dict[str, Any]]:
        return self.bible.get("relationships", [])


def load_persona(cfg: dict[str, Any]) -> Persona:
    """从 config.paths 指定的文件加载 Persona。"""
    p = cfg["paths"]
    with open(ROOT / p["bible"], "r", encoding="utf-8") as f:
        bible = yaml.safe_load(f)
    with open(ROOT / p["timeline"], "r", encoding="utf-8") as f:
        timeline = yaml.safe_load(f)
    with open(ROOT / p["canon"], "r", encoding="utf-8") as f:
        canon = json.load(f)
    return Persona(bible=bible, timeline=timeline.get("events", []), canon=canon)


# ───────────────────────── 模型 ─────────────────────────

class ModelClient:
    """OpenAI 兼容客户端。无 key 时 is_usable=False，调用走 mock。"""

    def __init__(self, cfg: dict[str, Any], seed: int | None = None):
        self.cfg_model = cfg["model"]
        self.base_url = self.cfg_model.get("base_url") or None
        api_key_env = self.cfg_model.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = os.getenv(api_key_env)
        self.model = self.cfg_model.get("model", "gpt-4o-mini")
        self.temperature = self.cfg_model.get("temperature", 0.8)
        self.max_tokens = self.cfg_model.get("max_tokens", 1024)
        self.timeout = self.cfg_model.get("request_timeout", 120)
        self.seed = seed
        self.is_usable = bool(self.api_key)
        self._client = None
        if self.is_usable:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self.base_url, api_key=self.api_key, timeout=self.timeout
                )
            except Exception as e:  # noqa: BLE001
                print(f"[ModelClient] 初始化失败，回退 mock: {e}")
                self.is_usable = False

    def chat(self, messages: list[dict[str, str]], **gen_kwargs) -> str:
        """同步对话。无 client 时返回 mock 文本。

        reasoning 模型（如 glm-5.2）会先输出思考 reasoning_content、再输出正文 content。
        本方法只取正文 content；若 content 为空（思考吃满 max_tokens）则回退取 reasoning。
        调用方可通过 gen_kwargs 传 max_tokens；reasoning 模型应给较大值。
        """
        if not self.is_usable:
            return self._mock(messages)
        for attempt in range(3):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=gen_kwargs.get("temperature", self.temperature),
                    max_tokens=gen_kwargs.get("max_tokens", self.max_tokens),
                )
                msg = resp.choices[0].message
                # 正文优先；reasoning 模型正文常在 content，思考在 reasoning_content
                content = getattr(msg, "content", None) or ""
                if not content:
                    # 回退：思考内容兜底（至少有东西，便于排查）
                    content = getattr(msg, "reasoning_content", None) or ""
                return content
            except Exception as e:  # noqa: BLE001
                print(f"[ModelClient] 调用失败({attempt+1}/3): {e}")
        print("[ModelClient] 三次重试均失败，回退 mock。")
        return self._mock(messages)

    @staticmethod
    def _mock(messages: list[dict[str, str]]) -> str:
        """占位输出。注意：mock 仅用于验证管线，不验证命门。"""
        sysp = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "日记" in sysp:
            return "【mock】今天的日记占位文本，未接入真实模型。"
        return json.dumps(
            [{"role": "other", "text": "【mock】你好"}, {"role": "person", "text": "【mock】嗯"}],
            ensure_ascii=False,
        )


# ───────────────────────── JSON 解析 ─────────────────────────

def parse_json_lenient(text: str) -> Any:
    """容错解析：去 ```json 代码块，找首个平衡的 {} 或 []。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 直接尝试
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 扫描首个平衡括号
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"无法从文本解析 JSON: {text[:120]}…")


# ───────────────────────── 正典块渲染 ─────────────────────────

def render_identity_block(persona: Persona) -> str:
    b = persona.bible
    lines = []
    idn = b["identity"]
    lines.append(f"姓名：{idn['name']}（{idn['gender']}），{idn['birth_date']} 生于 {idn['hometown']}，现居 {idn['current_city']}，{idn['occupation']}。")
    if b.get("education"):
        e = b["education"][0]
        lines.append(f"教育：{e['school']} {e['major']}（{e['start']}–{e['end']}）。")
    if b.get("family"):
        lines.append("家人：" + "；".join(f"{f['relation']}{f['name']}（{f['note']}）" for f in b["family"]) + "。")
    if b.get("relationships"):
        lines.append("关系网：" + "；".join(f"{r['relation']}{r['name']}（{r['note']}）" for r in b["relationships"]) + "。")
    return "\n".join(lines)


def render_canon_block(canon: dict[str, Any]) -> str:
    """把 canon.json 的事实表渲染为给 prompt 的事实约束块。"""
    facts = canon.get("facts", {})
    lines = ["【必须严格遵守的事实正典（不得矛盾）】"]
    for key, spec in facts.items():
        val = spec.get("value")
        lines.append(f"- {key}：{val}")
    return "\n".join(lines)


def render_voice_block(persona: Persona) -> str:
    v = persona.bible["voice"]
    lines = ["【口吻规范】"]
    lines.append(f"语域：{v['register']}。")
    lines.append(f"用词：{v['vocabulary']}。")
    lines.append(f"口头禅/犹豫：{', '.join(v.get('tics', []))}。")
    lines.append(f"句长：{v['sentence_length']}。")
    lines.append(f"标点：{v['punctuation']}。")
    lines.append(f"emoji：{'允许' if v.get('emoji') else '禁止'}；markdown 结构：{'允许' if v.get('markdown') else '禁止'}。")
    if v.get("catchphrases"):
        lines.append(f"口头禅：{', '.join(v['catchphrases'])}。")
    if v.get("forbid_assistant_speak"):
        lines.append("严禁出现助手腔：" + "、".join(v["forbid_assistant_speak"]) + " 等任何 AI 助手口吻。")
    return "\n".join(lines)


def render_prompt(template_name: str, **tokens) -> str:
    """读 prompts/<name>.txt，按 {{TOKEN}} 占位符替换。"""
    with open(PROMPTS_DIR / f"{template_name}.txt", "r", encoding="utf-8") as f:
        tpl = f.read()
    for k, v in tokens.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl
