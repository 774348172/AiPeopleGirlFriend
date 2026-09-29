from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path


RUBRIC = """你在审核中文角色聊天SFT训练目标。逐条判断目标回复是否可作为监督答案。
只按模型实际可见输入判断，不得借助常识补出具体个人事实。
accepted必须同时满足：事实有输入/场景/角色锚依据；主体来源时间正确；回应玩家真实意图；建议没有伪装成当前事实；没有把意愿说成已执行；自然连贯且只有对白。
rejected包括：虚构具体经历/时间/位置/行为结果；主体或时间错位；顺着问题预设编造；答非所问；动作旁白；危险建议；历史里已经出现无依据答案且当前目标继续依赖它。
玩家本轮明确陈述的当前事实可以作为依据。一般意见、情绪、偏好和条件式建议不要求外部事实。角色事实只能以实际可见的共享生产角色锚为准；深山身世等若出现在锚中可以使用，但锚只支持其明确原句，不能据此补造在那里学会某项技能等新经历。当前伤势、关系、动作和共同往事必须来自本轮运行时区块、历史或记忆，不能由静态角色锚代替。
只能输出JSON。每个输入编号必须恰好返回一次。decision只能是accepted或rejected。reason_code只用：supported,unsupported_fact,subject_source,time_relation,intent,action_completion,naturalness,safety,bad_history。
"""


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def dynamic_system(text: str) -> str:
    marker = "[当前权威事实]"
    if marker in text:
        return marker + text.split(marker, 1)[1]
    return text[-3500:]


def compact_case(index: int, row: dict, source_line: int) -> dict:
    messages = row["messages"]
    target_index = row["supervised_message_indexes"][-1]
    target = messages[target_index]["content"]
    history = []
    for i, message in enumerate(messages):
        if i == 0 or i == target_index:
            continue
        history.append({"role": message["role"], "content": message["content"]})
    return {
        "n": index,
        "sample_id": row["sample_id"],
        "source_line": source_line,
        "task_type": row.get("task_type"),
        "visible_system": dynamic_system(messages[0]["content"]),
        "history_and_current_user": history,
        "training_target": target,
    }


def call_ollama(model: str, cases: list[dict], timeout: int, challenge: bool = False) -> list[dict]:
    challenge_text = ""
    if challenge:
        challenge_text = (
            "\n这些条目已经被第一审标成accepted。你是反向复核者，必须主动寻找被漏掉的错误，"
            "尤其检查先前assistant是否已经编造、问句预设是否被当成事实、第一人称经历是否无依据、"
            "以及是否把提议或意愿说成已经完成。只有找不到明确违反S1-S6的证据时才能保留accepted。\n"
        )
    prompt = RUBRIC + challenge_text + "\n待审核条目：\n" + json.dumps(cases, ensure_ascii=False)
    schema = {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n": {"type": "integer"},
                        "decision": {"type": "string", "enum": ["accepted", "rejected"]},
                        "reason_code": {"type": "string", "enum": [
                            "supported", "unsupported_fact", "subject_source", "time_relation",
                            "intent", "action_completion", "naturalness", "safety", "bad_history"
                        ]},
                        "note": {"type": "string"},
                    },
                    "required": ["n", "decision", "reason_code", "note"],
                },
            }
        },
        "required": ["decisions"],
    }
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 32768, "num_predict": 1800},
        "keep_alive": "30m",
    }, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return json.loads(body["response"])["decisions"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--challenge", action="store_true")
    args = parser.parse_args()

    candidates = read_jsonl(args.candidate)
    source_rows = read_jsonl(args.source)
    source_lines = {row["sample_id"]: i for i, row in enumerate(source_rows, 1)}
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    done = {row["sample_id"] for row in ledger["decisions"]}
    remaining = [row for row in candidates if row["sample_id"] not in done]
    prior = {}
    if args.output.exists():
        prior_doc = json.loads(args.output.read_text(encoding="utf-8"))
        prior = {row["sample_id"]: row for row in prior_doc.get("decisions", [])}
    pending = [row for row in remaining if row["sample_id"] not in prior]
    print(f"remaining={len(remaining)} resume={len(prior)} pending={len(pending)}", flush=True)

    for start in range(0, len(pending), args.batch_size):
        rows = pending[start:start + args.batch_size]
        cases = [compact_case(i, row, source_lines[row["sample_id"]]) for i, row in enumerate(rows)]
        expected = set(range(len(cases)))
        for attempt in range(3):
            try:
                results = call_ollama(args.model, cases, args.timeout, args.challenge)
                mapped = {item["n"]: item for item in results}
                if set(mapped) != expected:
                    raise ValueError(f"returned indexes {sorted(mapped)}, expected {sorted(expected)}")
                break
            except Exception as exc:
                if attempt == 2:
                    raise
                print(f"retry batch {start}: {exc}", flush=True)
                time.sleep(2)
        for case, row in zip(cases, rows):
            result = mapped[case["n"]]
            prior[row["sample_id"]] = {
                "sample_id": row["sample_id"],
                "source_line": case["source_line"],
                "decision": result["decision"],
                "reason_code": result["reason_code"],
                "note": result["note"],
            }
        document = {
            "kind": "local_model_semantic_audit_proposals",
            "model": args.model,
            "candidate_sha256": hashlib.sha256(args.candidate.read_bytes()).hexdigest(),
            "decisions": list(prior.values()),
        }
        args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"audited={len(prior)}/{len(remaining)}", flush=True)


if __name__ == "__main__":
    main()
