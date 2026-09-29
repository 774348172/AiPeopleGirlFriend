"""Derive an immutable frontend SFT package from reviewed legacy sources."""
from __future__ import annotations
import argparse
import copy
import json
import re
from collections import Counter
from pathlib import Path

from build_game_reply_preview import ROOT, PROFILE, export_row, production_messages, digest, json_text
from data_gen_v4.runtime_grounded.renderer import render_game_reply_context_from_model_view
from runtime.world_mind.game_reply_context import build_game_reply_chat_messages, parse_game_reply_dialogue_lines
from build_v5_pilot_dataset import _load_baiweixi_render_context, _scenario, Concept

DATA = ROOT / "训练数据"
PILOT = DATA / "baiweixi_v5_pilot_v1"
PREVIEW = DATA / "game_reply_preview24_20260908"
SUPPLEMENT = DATA / "baiweixi_v5_targeted_supplement_v1"
LEGACY = ROOT.parent / "AiPeople/training_packages/training_package_baiweixi_qwen35_27b/data/baiweixi_27b_ready.jsonl"


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def signature(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_contract(scenario):
    scenario = copy.deepcopy(scenario)
    task = scenario["task_type"]
    scenario["oracle_view"]["response_contract"] = {
        "unknown_acknowledgement": "required" if task == "reply_insufficient_information" else "not_applicable",
        "subject_proposition_binding": "required" if task == "reply_subject_attribution" else "not_applicable",
        "unsupported_expansion_policy": "reject",
    }
    # Old unknown records encoded a missing date as an unknown object location.
    if task == "reply_insufficient_information" and "哪一天" in scenario["model_view"]["current_protagonist_utterance"]:
        for fact in scenario["model_view"].get("unknown_facts", []):
            fact["predicate"] = "日期"
    return scenario


def prepare(review_path: Path, output: Path):
    review = json.loads(review_path.read_text(encoding="utf-8"))
    original = json.loads((PREVIEW / "preview.json").read_text(encoding="utf-8"))
    if review["dataset_id"] != original["dataset_id"] or review["regression_sha256"] != original["regression_sha256"]:
        raise ValueError("Wrong preview review")
    if len(review["cases"]) != 24 or any(c["rating"] != "好" for c in review["cases"]):
        raise ValueError("Preview human approval incomplete")
    for actual, expected in zip(review["cases"], original["cases"], strict=True):
        if actual["sample_id"] != expected["sample_id"] or actual["messages"] != expected["messages"]:
            raise ValueError("Reviewed inputs changed")
    freeze = json.loads((PILOT / "package_freeze_manifest_20260831.json").read_text(encoding="utf-8"))
    for relative, expected in freeze["package_files_sha256"].items():
        if digest((PILOT / relative).read_bytes()) != expected:
            raise ValueError(f"Frozen pilot changed: {relative}")
    _, _, anchor = _load_baiweixi_render_context()
    if not original["cases"][0]["messages"][0]["content"].startswith(anchor + "\n\n"):
        raise ValueError("Approved character anchor changed")
    regression = json.loads((PREVIEW / "regression_review12.json").read_text(encoding="utf-8"))
    reserved_questions = {c["question"] for c in regression["cases"]}
    reserved_words = ("借书证", "加湿器", "复诊预约")
    packages = {name: read_rows(PILOT / "package" / f"{name}.jsonl") for name in ("train", "dev", "test", "sealed")}
    excluded, train, dev, test = [], [], [], []
    heldout_dialogues = {signature(row["conversations"][1:]) for split in ("dev", "test", "sealed") for row in packages[split]}
    heldout_views = []
    for split in ("dev", "test", "sealed"):
        for row in packages[split]:
            try:
                heldout_views.append(json.loads(row["conversations"][1]["value"]))
            except (ValueError, KeyError):
                pass
    heldout_labels = {m["subject_display_name"] for v in heldout_views for m in v.get("selected_memory_frame", {}).get("selected_memories", []) if m.get("subject_display_name")}

    def add(scenario, target, source, family, split="train"):
        model_text = signature(scenario["model_view"])
        if any(word in model_text or word in target["reply"] for word in reserved_words) or scenario["model_view"]["current_protagonist_utterance"] in reserved_questions:
            excluded.append({"id": scenario["scenario_id"], "reason": "reserved_regression_concept"})
            return
        if source == "persona":
            view = scenario["model_view"]
            context = render_game_reply_context_from_model_view(view)
            turns = build_game_reply_chat_messages(parse_game_reply_dialogue_lines(view["recent_dialogue"]),
                character_display_name="白未晞", current_protagonist_utterance=view["current_protagonist_utterance"])
            messages = [{"role": "system", "content": anchor + "\n\n" + context}]
            messages.extend({"role": role, "content": text} for role, text in turns)
            if messages != production_messages(view, anchor):
                raise ValueError("Static production input mismatch")
            messages.append({"role": "assistant", "content": target["reply"]})
            row = {"sample_id": scenario["scenario_id"], "render_profile_id": PROFILE,
                "messages": messages, "supervised_message_indexes": [len(messages) - 1]}
        else:
            row = export_row(scenario, target, anchor)
        row.update(source=source, task_type=scenario["task_type"], family_id=family)
        if any(m["content"].lstrip().startswith('{"') for m in row["messages"]):
            excluded.append({"id": row["sample_id"], "reason": "embedded_json"})
            return
        {"train": train, "dev": dev, "test": test}[split].append(row)

    # Retain the original pilot split; migrate only its selected training rows.
    selected = {signature(row["conversations"]): name for name in ("train", "dev", "test") for row in packages[name]}
    for candidate in read_rows(PILOT / "inputs/grounded_candidates.jsonl"):
        scenario, target = candidate["scenario"], candidate["teacher_target"]
        conv = [{"from": "system", "value": "你是白未晞。只输出角色说出口的自然回复。"},
                {"from": "human", "value": signature(scenario["model_view"])},
                {"from": "gpt", "value": target["reply"].strip()}]
        split = selected.get(signature(conv))
        if split is None:
            raise ValueError(f"Unmapped audited source: {scenario['scenario_id']}")
        if candidate["semantic_audit"]["decision"] != "approve":
            raise ValueError("Unapproved legacy target")
        add(normalize_contract(scenario), target, "grounded", next(a for a in scenario["split_anchors"] if a.startswith("family:")), split)
    for candidate in read_rows(SUPPLEMENT / "candidates.jsonl"):
        if candidate["semantic_audit"]["decision"] != "approve":
            raise ValueError("Unapproved supplement target")
        scenario = normalize_contract(candidate["scenario"])
        add(scenario, candidate["teacher_target"], "grounded", next(a for a in scenario["split_anchors"] if a.startswith("family:")))
    for candidate in read_rows(PREVIEW / "sources.jsonl"):
        scenario = candidate["scenario"]
        add(scenario, candidate["teacher_target"], "human_preview", next(a for a in scenario["split_anchors"] if a.startswith("family:")))

    legacy_rows = read_rows(LEGACY)
    metadata = read_rows(DATA / "baiweixi_v4_final.metadata.jsonl")
    if len(legacy_rows) != 1973 or len(metadata) != 1973:
        raise ValueError("Legacy source alignment changed")
    for index, (row, meta) in enumerate(zip(legacy_rows, metadata, strict=True)):
        conv = row["conversations"]
        content = signature(conv[1:])
        sid = meta["sample_id"]
        if content in heldout_dialogues or any(word in content for word in reserved_words) or any(label in content for label in heldout_labels):
            excluded.append({"id": sid, "reason": "heldout_content_or_concept"})
            continue
        replies = "\n".join(m["value"] for m in conv if m["from"] == "gpt")
        if re.search(r"伤好.{0,8}(就走|离开|会走)|伤好了再走", replies):
            excluded.append({"id": sid, "reason": "outdated_leave_when_healed_persona"})
            continue
        family = str(meta.get("family_id") or sid)
        split = "dev" if int(digest(family.encode())[:8], 16) % 20 == 0 else "train"
        for turn_index, message in enumerate(conv):
            if message["from"] != "gpt":
                continue
            if turn_index < 2 or conv[turn_index - 1]["from"] != "human":
                raise ValueError(f"Invalid legacy turn: {sid}")
            scenario = _scenario(Concept("static.seed", "static_context", "日常对话", "context", "当前", "过去"), "reply_direct_answer", 12000 + index, sealed=False)
            scenario["scenario_id"] = f"frontend.static.{index:04d}.{turn_index}"
            view = scenario["model_view"]
            view["game_feedback"] = []
            view["snapshot"].pop("captured_game_time")
            view["snapshot"]["scene"]["location_label"] = "未提供"
            view["snapshot"]["protagonist"].update(location_label="未提供", activity="未提供")
            for key in view["previous_heroine_runtime"]["living_mind"]:
                view["previous_heroine_runtime"]["living_mind"][key] = "未提供"
            view["previous_heroine_runtime"]["relationship"].update(stage="未提供", trust="未提供", unresolved_tension="")
            view["snapshot"]["world_state"]["scene_text"] = str(meta.get("scene") or "出租屋内的日常对话。")
            view["previous_heroine_runtime"]["living_mind"]["body"] = "外伤恢复大半，日常活动基本正常，快速行动仍可能轻微疼痛。"
            view["recent_dialogue"] = [("男主：" if m["from"] == "human" else "白未晞：") + m["value"] for m in conv[1:turn_index-1]]
            view["current_protagonist_utterance"] = conv[turn_index - 1]["value"]
            target = {"reply": message["value"], "declared_claims": []}
            before = len(train if split == "train" else dev)
            add(scenario, target, "persona", "v4:" + family, split)
            destination = train if split == "train" else dev
            if len(destination) > before:
                destination[-1].update(task_type=meta["task_type"], legacy_sample_id=sid)
    # An identical prompt/target must not receive accidental extra weighting.
    seen = {}
    for name, items in (("dev", dev), ("test", test), ("train", train)):
        kept = []
        for row in items:
            key = signature(row["messages"])
            if key in seen:
                excluded.append({"id": row["sample_id"], "reason": "duplicate_messages", "kept_split": seen[key]})
            else:
                seen[key] = name
                kept.append(row)
        items[:] = kept
    train_families = {r["family_id"] for r in train}
    if train_families & {r["family_id"] for r in dev + test}:
        raise ValueError("Family split overlap")
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in (("train", train), ("dev", dev), ("test", test), ("excluded", excluded)):
        (output / f"{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    for name, path in (("preview_human_review.json", review_path), ("regression_review12.json", PREVIEW / "regression_review12.json")):
        (output / name).write_bytes(path.read_bytes())
    sources = [LEGACY, DATA / "baiweixi_v4_final.metadata.jsonl", PILOT / "inputs/grounded_candidates.jsonl", SUPPLEMENT / "candidates.jsonl", PREVIEW / "sources.jsonl"]
    manifest = {"dataset_id": output.name, "render_profile_id": PROFILE, "status": "ready_for_candidate_training",
        "preview_human_approval": "24/24", "splits": {"train": len(train), "dev": len(dev), "test": len(test)},
        "training_sources": dict(Counter(r["source"] for r in train)), "exclusions": dict(Counter(r["reason"] for r in excluded)),
        "production_message_equality": True, "supervision": "each original assistant expanded into a final-only sample",
        "full_dataset_human_review": False, "quality_scope": "historical target audits plus approved preview; format migration, not new semantic re-audit",
        "static_context_scope": "unknown clock/location/activity; original metadata scene and legacy injury state; not recorded live trajectories",
        "sealed_policy": "legacy sealed untouched and excluded; 12 reviewed regressions excluded by question and concept; related templates are not independent generalization evidence",
        "source_sha256": {str(p): digest(p.read_bytes()) for p in sources},
        "files": {p.name: digest(p.read_bytes()) for p in output.iterdir()}}
    (output / "manifest.json").write_text(json_text(manifest), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.review, args.output)
