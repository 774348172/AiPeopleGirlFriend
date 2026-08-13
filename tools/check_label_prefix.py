# -*- coding: utf-8 -*-
"""A-1：标签前缀实测（2026-08-06，并入 T4）。

问题：v1 训练数据 33.8% 的回复带 【emotion:xx】【voice:xx】 标签前缀并被烘焙进训练文本，
当前 GGUF（v1 训练）在运行时是否还会输出这类前缀？

方法：启动本地 llama-server（与 chat02 执行契约同配置），用运行时锚 + 10 条盲评题，
temperature 0.75（experience lane），统计输出中以 【 开头的标签前缀比例。

输出：{tagged, total, tagged_ratio, samples}，结论落盘到 设计文档/T4验收报告 引用。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# AI 程序侧资源（llama-server / GGUF / 评测文本）只读引用（2026-08-07 目录整理）
AI_PEOPLE_ROOT = Path(r"F:\AiPeople")
sys.path.insert(0, str(ROOT))

SERVER = AI_PEOPLE_ROOT / "local_runtime/llama.cpp/b10256-cuda-12.4/llama-server.exe"
MODEL = AI_PEOPLE_ROOT / "训练结果/qinweixi_windows_deploy/qinweixi_windows_deploy/qinweixi-q4_k_m.gguf"
PORT = 18081
URL = f"http://127.0.0.1:{PORT}/v1/chat/completions"

LABEL_RE = re.compile(r"^\s*【[^】]+】")


def load_questions() -> list[str]:
    """取 chat02e ab_packets 前 10 个 unit 的 user 台词。"""
    questions = []
    for line in (AI_PEOPLE_ROOT / "eval/chat02/human/chat02e-v1/public/ab_packets.jsonl").read_text(encoding="utf-8").splitlines():
        unit = json.loads(line)
        for message in unit.get("prompt", []):
            if message.get("role") == "user":
                questions.append(message["content"])
                break
        if len(questions) >= 10:
            break
    return questions


def main() -> int:
    sys.path.insert(0, str(AI_PEOPLE_ROOT))  # runtime._prompt 锚快照在 AI 程序侧
    from runtime._prompt import QIN_WEIXI_REPLY_SYSTEM

    server = subprocess.Popen(
        [
            str(SERVER),
            "-m", str(MODEL),
            "--host", "127.0.0.1",
            "--port", str(PORT),
            "-ngl", "99",
            "-c", "4096",
            "--flash-attn", "on",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # 等待就绪（最多 120s）
        ready = False
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
                ready = True
                break
            except Exception:
                time.sleep(1)
        if not ready:
            print("[error] llama-server 未就绪")
            return 1

        results = []
        for q in load_questions():
            body = json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": QIN_WEIXI_REPLY_SYSTEM},
                        {"role": "user", "content": q},
                    ],
                    "temperature": 0.75,
                    "top_p": 0.9,
                    "max_tokens": 160,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                URL, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                text = data["choices"][0]["message"]["content"] or ""
            except Exception as error:  # noqa: BLE001
                text = f"[error] {error}"
            results.append({"question": q, "output": text, "label_prefix": bool(LABEL_RE.match(text))})

        tagged = sum(1 for r in results if r["label_prefix"])
        summary = {
            "total": len(results),
            "tagged": tagged,
            "tagged_ratio": round(tagged / len(results), 3) if results else 0,
            "samples": results,
        }
        out = ROOT / "local_runtime/a1_label_prefix_result.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"标签前缀: {tagged}/{len(results)}（{summary['tagged_ratio']:.1%}）→ {out}")
        for r in results:
            print(f"  {'TAG' if r['label_prefix'] else '   '} | {r['question'][:24]} → {r['output'][:60]}")
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
