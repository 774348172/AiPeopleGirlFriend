"""人格保真度评测（InCharacter 思路，M1 训练后跑）。

做法：用大五（OCEAN）量表条目，让"训练后的模型以本人身份"自评 1-5，聚合后比对
bible 里的目标 OCEAN，看方向是否一致。方向一致率高 = 人格确实落进了权重。

前置：M1 训练完，用 LLaMA-Factory 起一个 chat 服务（OpenAI 兼容），例如：
  llamafactory-cli api training/configs/qwen3_4b_qlora.yaml   # 起 api server
  或合并后用 vLLM/Ollama 暴露 /v1/chat/completions。

用法:
  python -m eval.personality_fidelity --base-url http://localhost:8000/v1 --model qwen3-4b-persona

注：这是脚手架。LLM 自评有默许偏差（acquiescence），正式评测应扩充条目库 +
对照基座原模型做差分。当前给出结构与小条目集，便于先跑通。
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]  # F:\AiPeople（AI 程序侧）


def load_config(path: str = "config.yaml") -> dict:
    """读取 AI 程序侧 config.yaml（2026-08-07 生成器移出后内联，替代 data_gen.common）。"""
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass  # .env 可选；密钥可直接经环境变量提供
    with open(ROOT / path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.getenv("OPENAI_BASE_URL"):
        cfg["model"]["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.getenv("STRONG_MODEL"):
        cfg["model"]["model"] = os.environ["STRONG_MODEL"]
    return cfg


def load_bible(cfg: dict) -> dict:
    """读取人物设定 bible.yaml（替代 data_gen.common.load_persona）。"""
    with open(ROOT / cfg["paths"]["bible"], encoding="utf-8") as f:
        return yaml.safe_load(f)


class ModelClient:
    """OpenAI 兼容客户端（内联自 data_gen.common.ModelClient，只保留 chat 路径）。"""

    def __init__(self, cfg: dict):
        m = cfg["model"]
        self.base_url = m.get("base_url") or None
        api_key_env = m.get("api_key_env", "OPENAI_API_KEY")
        self.api_key = os.getenv(api_key_env)
        self.model = m.get("model", "gpt-4o-mini")
        self.temperature = m.get("temperature", 0.8)
        self.max_tokens = m.get("max_tokens", 1024)
        self.timeout = m.get("request_timeout", 120)
        self.is_usable = bool(self.api_key)
        self._client = None
        if self.is_usable:
            from openai import OpenAI

            self._client = OpenAI(
                base_url=self.base_url, api_key=self.api_key, timeout=self.timeout
            )

    def chat(self, messages: list[dict[str, str]], **gen_kwargs) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=gen_kwargs.get("temperature", self.temperature),
            max_tokens=gen_kwargs.get("max_tokens", self.max_tokens),
        )
        msg = resp.choices[0].message
        content = getattr(msg, "content", None) or ""
        if not content:  # reasoning 模型兜底
            content = getattr(msg, "reasoning_content", None) or ""
        return content

# 大五条目库（正向记分：同意=高分代表该特质高；负向：同意=高分代表该特质低，记分时翻转）
# trait: O开放 C尽责 E外向 A宜人 N神经质
ITEMS = [
    # O 开放
    ("O", +1, "我喜欢思考新点子，对艺术和美很敏感。"),
    ("O", -1, "我更愿意做熟悉稳妥的事，不太去碰没试过的东西。"),
    # C 尽责
    ("C", +1, "我做事会按计划来，不太拖延。"),
    ("C", -1, "我常常定下目标却拖着不开始。"),
    # E 外向
    ("E", +1, "和人待在一起、热热闹闹的场合让我有劲。"),
    ("E", -1, "比起聚会，我更愿意一个人待着。"),
    # A 宜人
    ("A", +1, "我容易体谅别人，不太和人起冲突。"),
    ("A", -1, "别人提要求时，我心里常常想拒绝但又开不了口。"),
    # N 神经质
    ("N", +1, "我容易焦虑，夜里偶尔会胡思乱想睡不着。"),
    ("N", -1, "我情绪挺稳，不太会因为小事心烦。"),
]

TRAIT_CN = {"O": "开放性", "C": "尽责性", "E": "外向性", "A": "宜人性", "N": "神经质"}


def ask_rating(client, model: str, item: str) -> int | None:
    """让被测模型以本人身份对条目给 1-5。"""
    system = (
        "下面是一句描述。请你以你自己（这个人物）的真实情况，判断你有多大程度符合，"
        "只回答一个 1 到 5 的整数：1=完全不符合，5=完全符合。只输出这个数字，不要别的字。"
    )
    # 注：实际跑时应注入人格锚（姓名等），但人格主体应在权重里；这里测的就是权重是否生效。
    try:
        resp = client.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": item},
            ],
            temperature=0.0,
            max_tokens=8,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[err] {e}")
        return None
    m = re.search(r"[1-5]", resp)
    return int(m.group()) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="qwen3-4b-persona")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    args = ap.parse_args()

    cfg = load_config()
    bible = load_bible(cfg)
    bible_ocean = bible["big_five"]  # {openness:8, ...}

    # 构造一个指向被测服务的 ModelClient
    os.environ["OPENAI_API_KEY"] = os.getenv(args.api_key_env) or "EMPTY"
    cfg["model"]["base_url"] = args.base_url
    cfg["model"]["model"] = args.model
    cfg["model"]["temperature"] = 0.0
    client = ModelClient(cfg)
    if not client.is_usable:
        raise SystemExit("无法连接被测服务，检查 --base-url / api key")

    from collections import defaultdict
    agg: dict[str, list[int]] = defaultdict(list)

    for trait, sign, item in ITEMS:
        r = ask_rating(client, args.model, item)
        if r is None:
            print(f"  [{trait}] '{item[:18]}…' -> 解析失败")
            continue
        score = r if sign > 0 else 6 - r  # 负向翻转
        agg[trait].append(score)
        print(f"  [{trait}] '{item[:18]}…' -> raw={r} norm={score}")

    # 比对方向
    key_map = {"O": "openness", "C": "conscientiousness", "E": "extraversion",
               "A": "agreeableness", "N": "neuroticism"}
    print("\n=== 人格保真度（方向比对 bible OCEAN）===")
    print(f"bible: {bible_ocean}")
    hits = 0
    for trait, scores in agg.items():
        if not scores:
            continue
        avg = sum(scores) / len(scores)
        target = bible_ocean[key_map[trait]]
        # bible 是 1-10；把模型均值(1-5)也映射到 1-10 做方向比对
        model_mapped = (avg - 1) / 4 * 9 + 1
        direction_ok = (model_mapped >= 5.5) == (target >= 5.5)
        hits += direction_ok
        print(f"  {TRAIT_CN[trait]}({trait}): 模型≈{model_mapped:.1f} bible={target} 方向{'✓' if direction_ok else '✗'}")
    print(f"\n方向一致: {hits}/{len(agg)}")
    print("注：脚手架级评测。正式应扩条目库 + 对照未微调基座做差分（InCharacter 方法）。")


if __name__ == "__main__":
    main()
