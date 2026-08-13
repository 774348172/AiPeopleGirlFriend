# -*- coding: utf-8 -*-
"""从真实对话生成 MEMORY_RERANK 训练样本（2026-08-11，SELECT-01 配套）。

从已人工复核通过的 final jsonl 读真实对话 → 注入 query → 教师标注候选记忆
→ 写入 sqlite（兼容 build_rerank_review_page.py 的复核页生成）。

用法:
  OPENAI_API_KEY=xxx python tools/gen_real_rerank.py \
      --data 训练数据/baiweixi_v4_1000_final.jsonl \
      --meta 训练数据/baiweixi_v4_1000_final.metadata.jsonl \
      --count 40 --seed 20260811 \
      --out 训练数据/_rerank_real40
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.rerank import distill_candidates


def load_conversations(data_path: Path, meta_path: Path) -> list[dict]:
    rows = [json.loads(l) for l in data_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    metas = [json.loads(l) for l in meta_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = []
    for row, meta in zip(rows, metas):
        if meta.get("task_type") == "rerank_memory":
            continue
        conv = row.get("conversations") or []
        messages = [
            {"role": "human" if m.get("from") == "human" else "assistant", "content": m.get("value", "")}
            for m in conv if m.get("from") != "system"
        ]
        if len(messages) < 2:
            continue
        out.append({
            "messages": messages,
            "scene": meta.get("scene", ""),
            "task_type": meta.get("task_type", ""),
            "sample_id": meta.get("sample_id", ""),
            "topic": meta.get("topic", ""),
        })
    return out


def pick_diverse(convs: list[dict], n: int, seed: int) -> list[dict]:
    from collections import defaultdict
    by_task = defaultdict(list)
    for c in convs:
        by_task[c["task_type"]].append(c)
    total = len(convs)
    picked = []
    for task, items in sorted(by_task.items()):
        quota = max(1, round(n * len(items) / total))
        items_sorted = sorted(items, key=lambda c: hashlib.sha256(
            (str(seed) + c["sample_id"]).encode("utf-8")).hexdigest())
        picked.extend(items_sorted[:quota])
    picked = sorted(picked, key=lambda c: hashlib.sha256(
        (str(seed) + c["sample_id"]).encode("utf-8")).hexdigest())[:n]
    return picked


def call_teacher(api_key: str, base_url: str, model: str, prompt: str, max_tokens: int = 8192) -> str:
    """调用 OpenAI 兼容 API（用 SDK，支持 extra_body 禁思考 + reasoning_content）。"""
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": prompt}],
        temperature=0.2,
        max_tokens=max_tokens,
        extra_body={"thinking": {"type": "disabled"}},
    )
    msg = response.choices[0].message
    content = getattr(msg, "content", None) or ""
    if not content or not content.strip():
        content = getattr(msg, "reasoning_content", None) or ""
    if not content or not content.strip():
        raise ValueError("API 返回空内容")
    return content

def write_sqlite(sqlite_path: Path, candidates: list[dict]) -> None:
    """写 candidate 记录到 sqlite（兼容 build_rerank_review_page 的格式）。"""
    con = sqlite3.connect(str(sqlite_path))
    con.execute("""CREATE TABLE IF NOT EXISTS v4_records (
        record_type TEXT, record_id TEXT, run_id TEXT, plan_id TEXT,
        payload_json TEXT, created_at TEXT
    )""")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for i, c in enumerate(candidates):
        payload = json.dumps(c, ensure_ascii=False)
        con.execute(
            "INSERT INTO v4_records VALUES (?,?,?,?,?,?)",
            ("candidate", f"rec-real-{i:04d}", "real-rerank", c.get("sample_id", ""), payload, now),
        )
    con.commit()
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", required=True)
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--base-url", default="https://aihub.lmdgame.com/api-product/v1")
    ap.add_argument("--model", default="deepseek-v4-flash")
    args = ap.parse_args()

    api_key = os.getenv(args.api_key_env)
    if not api_key:
        print(f"[error] 缺少 {args.api_key_env}")
        return 1

    # 1. 加载真实对话
    convs = load_conversations(Path(args.data), Path(args.meta))
    print(f"加载真实对话: {len(convs)} 条")
    picked = pick_diverse(convs, args.count, args.seed)
    print(f"抽样: {len(picked)} 条")

    # 2. 编译 package_set + snapshots（供 distill_candidates 用）
    import gen_v4
    from data_gen_v4.adapters.sources.registry import CompositeSourceLoader, FilePackageRegistry
    from data_gen_v4.core.compiler import GenerationPlanCompiler, RunSpec
    from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
    registry = FilePackageRegistry("profiles")
    loader = CompositeSourceLoader(".")
    compiler = GenerationPlanCompiler(
        registry, loader, RecipeDrivenItemFactory("profiles/baiweixi/pools.yaml"))
    pkg = gen_v4.build_package_set(Path("profiles"), "baiweixi")
    res = compiler.compile(RunSpec(run_id=f"real-rerank-{args.count}", seed=args.seed), pkg)
    context = res.context

    # 3. 加载 real 模式 prompt 模板
    prompt_template = (Path(__file__).resolve().parents[1] / "data_gen_v4" / "prompts" /
                       "memory_rerank_real.txt").read_text(encoding="utf-8")

    # 4. 逐条生成
    candidates_out = []
    completed = failed = 0
    for i, conv in enumerate(picked):
        # 提取 query
        humans = [m for m in conv["messages"] if m["role"] == "human"]
        if not humans:
            failed += 1
            continue
        current_msg = humans[-1]["content"]
        prior = []
        for m in conv["messages"]:
            if m is humans[-1]:
                break
            prior.append(m["content"])
        recent = prior[-4:]
        working_state = conv["scene"]

        # distill 候选（全库均衡，seed 变化）
        cands = distill_candidates(context, None, pool_size=8, seed=args.seed + i)
        if not cands:
            print(f"  [{i+1}] 候选为空，跳过")
            failed += 1
            continue

        # 渲染 prompt
        query_text = (
            f"场景：{conv['scene']}\n"
            f"工作状态：{working_state}\n"
            f"最近对话：{recent}\n"
            f"当前消息：{current_msg}"
        )
        candidates_text = "\n".join(
            f"- memory_id={c['memory_id']} | {c['selector_text']}" for c in cands)
        prompt = prompt_template.replace("{{QUERY}}", query_text).replace("{{CANDIDATES}}", candidates_text)

        # 调用教师
        try:
            raw = call_teacher(api_key, args.base_url, args.model, prompt)
        except Exception as e:
            print(f"  [{i+1}] API 异常: {e}")
            failed += 1
            continue

        # 解析 + 校验
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        try:
            target = json.loads(cleaned)
        except json.JSONDecodeError as e:
            print(f"  [{i+1}] JSON 解析失败: {e}")
            failed += 1
            continue

        # 强制 query 为注入值
        query = {"recent_dialogue": recent, "current_user_message": current_msg, "working_state": working_state}
        target["query"] = query

        # 清洗 labels：教师可能把 "memory_id | text" 整体当 id，取 | 前部分
        for l in target.get("labels", []):
            mid = str(l.get("memory_id", ""))
            if " | " in mid:
                l["memory_id"] = mid.split(" | ")[0].strip()
        # 校验 labels 覆盖
        cand_ids = {c["memory_id"] for c in cands}
        labelled_ids = {str(l.get("memory_id")) for l in target.get("labels", [])}
        missing = cand_ids - labelled_ids
        extra = labelled_ids - cand_ids
        if missing or extra:
            print(f"  [{i+1}] labels 覆盖不全: missing={missing} extra={extra}")
            failed += 1
            continue

        sample_id = f"real-rerank:{conv['sample_id']}"
        candidates_out.append({
            "sample_id": sample_id,
            "task_type": "rerank_memory",
            "input": {
                "scenario": {"scene": conv["scene"], "working_state": working_state},
                "candidates": cands,
                "real_conversation": True,
            },
            "target": target,
        })
        completed += 1
        if (i + 1) % 5 == 0:
            print(f"  进度: {i+1}/{len(picked)}（完成 {completed}）")

    # 5. 写 sqlite
    sqlite_path = Path(str(args.out) + ".sqlite")
    write_sqlite(sqlite_path, candidates_out)
    print(f"\n完成: completed={completed} failed={failed}")
    print(f"sqlite: {sqlite_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
