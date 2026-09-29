"""Freeze reviewed regression cases and build a human-gated frontend preview."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "AiPeople"))
from build_v5_pilot_dataset import Concept, _scenario, _load_baiweixi_render_context
from data_gen_v4.runtime_grounded.renderer import render_game_reply_training_record
from runtime.world_mind.model_payloads import build_game_reply_messages
from runtime.world_mind.game_reply_context import parse_game_reply_dialogue_lines

PROFILE = "GAME_REPLY_CONTEXT_V1"
CONCEPTS = (
    Concept("preview.sewing_box", "sewing_box", "针线盒", "location", "电视柜右侧抽屉", "卧室窗台"),
    Concept("preview.picnic_mat", "picnic_mat", "野餐垫", "location", "阳台储物箱", "门口鞋柜"),
    Concept("preview.photo_album", "photo_album", "相册", "location", "客厅矮柜", "床底纸箱"),
    Concept("preview.chess_set", "chess_set", "围棋盒", "location", "书房木架", "客厅茶几"),
    Concept("preview.baking_tray", "baking_tray", "烤盘", "location", "厨房吊柜", "餐桌下面"),
    Concept("preview.sewing_ruler", "sewing_ruler", "裁衣尺", "location", "缝纫机旁边", "工具箱里"),
)
TASKS = ("reply_accept_authoritative_update", "reply_resolve_world_memory_conflict",
         "reply_subject_attribution", "reply_insufficient_information")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def production_messages(view, anchor):
    """Exercise the production message builder with the same projected state."""
    snapshot = view["snapshot"]
    protagonist = snapshot["protagonist"]
    runtime = view["previous_heroine_runtime"]
    memories = [NS(**m) for m in view["selected_memory_frame"]["selected_memories"]]
    request = NS(
        prompt=NS(character_display_name="白未晞", game_reply_system_prompt=anchor),
        recent_dialogue=parse_game_reply_dialogue_lines(view.get("recent_dialogue", [])),
        approved_state=NS(living_mind=NS(**runtime["living_mind"]), relationship=NS(**runtime["relationship"])),
        approved_actions=tuple(NS(**a) for a in view.get("approved_heroine_actions", [])),
        snapshot=NS(
            captured_game_time=(datetime.fromisoformat(snapshot["captured_game_time"])
                                if snapshot.get("captured_game_time") else "未知时间"),
            protagonist=NS(**protagonist), scene=NS(**snapshot["scene"]),
            world_state=NS(**snapshot["world_state"]),
            selected_memory_frame=NS(selected_memories=memories),
            pending_actions=view.get("pending_actions", []),
            player_fact_reports=view.get("player_fact_reports", []),
            unknown_facts=view.get("unknown_facts", []),
            game_feedback=view.get("game_feedback", []),
            protagonist_utterance=view["current_protagonist_utterance"],
        ),
    )
    return list(build_game_reply_messages(request))


def export_row(scenario, target, anchor):
    record = render_game_reply_training_record(scenario, target, system_anchor=anchor)
    messages = [{"role": "system", "content": record.system_anchor}] + [
        {"role": "user" if m["role"] == "human" else m["role"], "content": m["content"]}
        for m in record.messages
    ]
    if messages[:-1] != production_messages(scenario["model_view"], anchor):
        raise ValueError(f"Production input differs: {record.sample_id}")
    return {"sample_id": record.sample_id, "render_profile_id": PROFILE,
            "messages": messages, "supervised_message_indexes": [len(messages) - 1]}


def candidates():
    for index, concept in enumerate(CONCEPTS):
        for task in TASKS:
            scenario = _scenario(concept, task, 9100 + index, sealed=False)
            scenario["scenario_id"] = scenario["scenario_id"].replace("pilot.", "frontend_preview.", 1)
            view = scenario["model_view"]
            kind = task
            if task == TASKS[0]:
                view["recent_dialogue"] = [f"男主：之前{concept.label}在{concept.stale}。", "白未晞：嗯，我知道了。"]
                view["current_protagonist_utterance"] = f"我刚看过了，{concept.label}在{concept.current}，不在{concept.stale}。"
                if index < 2:
                    view["game_feedback"] = [f"游戏确认：{concept.label}现在在{concept.current}。"]
                    reply = f"嗯，现在在{concept.current}。"
                    kind = "已确认更新"
                else:
                    view["player_fact_reports"] = [{"subject": concept.label, "predicate": "位置",
                        "value": concept.current, "reported_by": "男主", "status": "pending_confirmation"}]
                    reply = f"嗯，你刚看到它在{concept.current}。"
                    kind = "玩家报告待确认"
            elif task == TASKS[1]:
                view["current_protagonist_utterance"] = f"{concept.label}现在在哪儿？我记得以前在{concept.stale}。"
                view["game_feedback"] = [f"游戏确认：{concept.label}现在在{concept.current}。"]
                reply = f"现在在{concept.current}，{concept.stale}是之前的位置。"
                kind = "当前事实与旧记忆"
            elif task == TASKS[2]:
                actor = ("陈宁", "林悦", "宋琪", "许岚", "顾航", "周禾")[index]
                statement = f"{concept.label}在{concept.current}。"
                view["recent_dialogue"] = ["男主：你听到刚才那句话了吗？", f"{actor}：{statement}"]
                view["current_protagonist_utterance"] = f"刚才是谁说{concept.label}在{concept.current}？"
                reply = f"是{actor}说的，说{concept.label}在{concept.current}。"
                kind = "第三方归属"
            else:
                view["current_protagonist_utterance"] = f"{concept.label}是哪一天被挪到现在这个位置的？"
                view["unknown_facts"][0].update(subject=concept.label, predicate="位置改变的日期")
                for fact in scenario["oracle_view"]["facts"]:
                    fact["predicate"] = "location_changed_date"
                for assertion in scenario["oracle_view"]["required_assertions"]:
                    assertion["predicate"] = "location_changed_date"
                scenario["split_anchors"] = [
                    "predicate:location_changed_date" if item.startswith("predicate:") else item
                    for item in scenario["split_anchors"]
                ]
                reply = ("具体哪天我不清楚。", "不知道是哪一天。", "这个日期我不确定。",
                         "我没有听到具体是哪天。", "是哪天挪的，我不清楚。", "具体日期不知道。")[index]
                kind = "未知日期"
            target = {"reply": reply, "declared_claims": []}
            yield scenario, target, kind


def build(review_path: Path, output: Path):
    raw = review_path.read_bytes()
    reviewed = json.loads(raw)
    if len(reviewed["cases"]) != 12:
        raise ValueError("Expected 12 regression cases")
    scores = {arm: Counter(c["human_review"][arm] for c in reviewed["cases"])
              for arm in ("base", "old_adapter", "new_adapter")}
    if any(set(counts) - {"好", "差"} for counts in scores.values()):
        raise ValueError("Incomplete human review")
    _, _, anchor = _load_baiweixi_render_context()
    frozen_ids = {c["sample_id"] for c in reviewed["cases"]}
    frozen_inputs = {c["question"] for c in reviewed["cases"]}
    rows, sources, cases = [], [], []
    for scenario, target, kind in candidates():
        if scenario["scenario_id"] in frozen_ids or scenario["model_view"]["current_protagonist_utterance"] in frozen_inputs:
            raise ValueError("Regression sample contamination")
        row = export_row(scenario, target, anchor)
        if any(word in json_text(row["messages"]) for word in ("借书证", "加湿器", "复诊预约", "JUDGE_TURN", "snapshot_id", "oracle_view")):
            raise ValueError("Reserved concept or internal field leaked")
        rows.append(row)
        sources.append({"scenario": scenario, "teacher_target": target,
                        "target_origin": "deterministic_draft_for_human_review", "semantic_audit": "pending"})
        cases.append({**row, "category": kind, "rating": "", "note": ""})
    report = {"dataset_id": "game_reply_preview_24_20260908", "status": "awaiting_human_review",
              "regression_sha256": digest(raw), "training_authorized": False, "cases": cases}
    output.mkdir(parents=True, exist_ok=False)
    (output / "regression_review12.json").write_bytes(raw)
    (output / "candidates.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (output / "sources.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sources), encoding="utf-8")
    (output / "preview.json").write_text(json_text(report), encoding="utf-8")
    manifest = {"status": "awaiting_human_review", "training_started": False, "rows": len(rows),
                "categories": dict(Counter(c["category"] for c in cases)),
                "regression_scores": scores, "source_review": str(review_path),
                "regression_is_training_data": False, "production_message_equality": "24/24",
                "validation_scope": "production builder called with projected state; not live gameplay end-to-end",
                "human_semantic_audit": "pending", "files": {p.name: digest(p.read_bytes()) for p in output.iterdir()}}
    (output / "manifest.json").write_text(json_text(manifest), encoding="utf-8")
    page = Path(__file__).with_name("game_reply_preview.html").read_text(encoding="utf-8")
    (output / "review.html").write_text(page.replace("__REPORT__", json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.review, args.output)
