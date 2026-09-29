"""Attribute frozen Gemma V2 input tokens to their existing context sections."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_gemma_v2_9216_probe import read_json, sha_file  # noqa: E402

PACKAGE = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b_v2_2611_9216"
OUTPUT = (
    ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b"
    / "outputs" / "baiweixi_v2_2611_context_budget_20260922.json"
)
HEADING = re.compile(r"(?m)^\[[^\r\n]+\](?:\r?\n|$)")


def sections(text: str) -> list[tuple[str, str]]:
    matches = list(HEADING.finditer(text))
    values = [("系统前言", text[: matches[0].start()] if matches else text)]
    values.extend(
        (match.group().strip(), text[match.start() : matches[index + 1].start() if index + 1 < len(matches) else len(text)])
        for index, match in enumerate(matches)
    )
    return values


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def main() -> None:
    from transformers import AutoTokenizer
    from unsloth.chat_templates import get_chat_template

    manifest = read_json(PACKAGE / "manifest.json")
    for name, expected in manifest["files"].items():
        if sha_file(PACKAGE / name) != expected:
            raise ValueError(f"Frozen file changed: {name}")
    tokenizer = get_chat_template(
        AutoTokenizer.from_pretrained(manifest["model_path"], local_files_only=True),
        chat_template="gemma-4",
    )
    if hashlib.sha256(tokenizer.chat_template.encode("utf-8")).hexdigest() != manifest["chat_template_sha256"]:
        raise ValueError("Tokenizer template drift")
    stats: dict[str, list[int]] = defaultdict(list)
    by_scene: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    examples = []
    count = 0
    for split in ("train", "dev"):
        with (PACKAGE / "data" / f"{split}.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                count += 1
                messages = row["messages"]
                system = messages[0]["content"]
                breakdown: dict[str, int] = {}
                for name, content in sections(system):
                    size = len(tokenizer(content, add_special_tokens=False)["input_ids"])
                    breakdown[name] = breakdown.get(name, 0) + size
                    stats[name].append(size)
                    by_scene[row["scene"]][name].append(size)
                non_system = sum(
                    len(tokenizer(message["content"], add_special_tokens=False)["input_ids"])
                    for message in messages[1:]
                )
                stats["其他对话与目标回复"].append(non_system)
                stats["完整消息实际序列"].append(row["sequence_tokens"])
                stats["系统段独立计数和"].append(sum(breakdown.values()))
                if row["sample_id"] == manifest["longest_sequence"]["sample_id"] or count == 1:
                    examples.append({
                        "sample_id": row["sample_id"],
                        "sequence_tokens": row["sequence_tokens"],
                        "system_sections": breakdown,
                        "non_system_content_tokens": non_system,
                    })
    if count != sum(manifest["counts"].values()):
        raise ValueError(f"Row count changed: {count}")
    report = {
        "scope": "frozen_v2_context_token_attribution",
        "package_manifest_sha256": sha_file(PACKAGE / "manifest.json"),
        "rows": count,
        "counting_method": "Each existing system heading and non-system message content is tokenized independently with the frozen Gemma tokenizer; BPE boundary and chat-template overhead explain small differences from exact full-message sequence length. No input text or training data was changed.",
        "sections": {
            name: {
                "rows_present": len(values),
                "mean_tokens": round(sum(values) / len(values), 1),
                "p50_tokens": percentile(values, .50),
                "p90_tokens": percentile(values, .90),
                "max_tokens": max(values),
            }
            for name, values in sorted(stats.items(), key=lambda item: -sum(item[1]))
        },
        "scenes": {
            scene: {
                "rows": max(map(len, fields.values())),
                "top_system_sections_by_mean": [
                    {"name": name, "mean_tokens": round(sum(values) / len(values), 1)}
                    for name, values in sorted(fields.items(), key=lambda item: -sum(item[1]) / len(item[1]))[:5]
                ],
            }
            for scene, fields in sorted(by_scene.items())
        },
        "examples": examples,
    }
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "rows": count, "sections": report["sections"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
