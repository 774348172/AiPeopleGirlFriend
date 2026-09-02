"""Build the real V5 pilot inputs and package without starting training.

The script uses the B-block teacher/audit adapter for grounded replies, performs
an independent V5 review of static V4 records, freezes a disjoint sealed suite,
and finally invokes the C-block package builder. Every model result is persisted
so reruns resume instead of silently replacing prior decisions.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

import yaml
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.training import TrainingRecordV4, estimate_token_count  # noqa: E402
from data_gen_v4.runtime_grounded import (  # noqa: E402
    RuntimeGroundedReplyAdapter,
    build_pilot_package,
    evaluate_runtime_grounded_candidate,
    validate_scenario_collection,
)

TASKS = (
    "reply_accept_authoritative_update",
    "reply_reject_stale_claim",
    "reply_correct_false_premise",
    "reply_resolve_world_memory_conflict",
    "reply_confirm_current_state",
    "reply_subject_attribution",
    "reply_insufficient_information",
    "reply_direct_answer",
)
TASK_SLUG = {
    "reply_accept_authoritative_update": "accept",
    "reply_reject_stale_claim": "reject",
    "reply_correct_false_premise": "correct",
    "reply_resolve_world_memory_conflict": "conflict",
    "reply_confirm_current_state": "confirm",
    "reply_subject_attribution": "subject",
    "reply_insufficient_information": "unknown",
    "reply_direct_answer": "direct",
}


@dataclass(frozen=True)
class Concept:
    key: str
    subject: str
    label: str
    predicate: str
    current: str
    stale: str
    domain: str = "objective_world"


TRAIN_CONCEPTS = (
    Concept("umbrella.location", "folding_umbrella", "折叠伞", "location", "玄关伞架", "卧室门后"),
    Concept("thermometer.location", "thermometer", "体温计", "location", "客厅药箱", "床头抽屉"),
    Concept("laundry.mode", "washing_machine", "洗衣机", "operating_mode", "漂洗", "脱水"),
    Concept("lamp.state", "desk_lamp", "书桌灯", "state", "已关闭", "亮着"),
    Concept("parcel.status", "parcel_order", "快递", "delivery_status", "驿站待取", "已送上门"),
    Concept("kettle.state", "electric_kettle", "电水壶", "state", "保温中", "正在烧水"),
    Concept("medicine.time", "protagonist", "你的胃药", "medicine_time", "晚饭后", "睡前", "protagonist_plan"),
    Concept("meeting.time", "protagonist", "你的组会", "meeting_time", "星期三下午两点", "星期二上午九点", "protagonist_plan"),
    Concept("train.time", "protagonist", "你的火车", "departure_time", "下午四点二十", "下午三点", "protagonist_plan"),
    Concept("book.location", "borrowed_book", "借来的书", "location", "书架第二层", "餐桌上"),
    Concept("charger.location", "phone_charger", "手机充电器", "location", "沙发旁插座", "书桌抽屉"),
    Concept("window.state", "kitchen_window", "厨房窗户", "state", "留了一条缝", "完全关着"),
    Concept("rice.status", "rice_cooker", "电饭煲", "operating_mode", "预约煮饭", "保温"),
    Concept("soup.status", "soup_pot", "汤锅", "cooking_status", "小火炖着", "已经关火"),
    Concept("cat_food.location", "cat_food", "猫粮", "location", "储物柜下层", "冰箱旁"),
    Concept("scarf.location", "white_scarf", "白围巾", "location", "衣帽架", "纸箱里"),
    Concept("keys.location", "spare_keys", "备用钥匙", "location", "玄关挂钩", "厨房抽屉"),
    Concept("tea.preference", "protagonist", "你的茶", "tea_preference", "淡一点", "浓一点", "protagonist_preference"),
    Concept("porridge.preference", "protagonist", "你的粥", "porridge_preference", "不放糖", "放红糖", "protagonist_preference"),
    Concept("alarm.time", "protagonist", "你的闹钟", "alarm_time", "早上七点半", "早上六点半", "protagonist_plan"),
    Concept("movie.time", "protagonist", "电影票", "show_time", "星期日晚上七点", "星期六晚上八点", "protagonist_plan"),
    Concept("repair.status", "desk_fan", "桌面风扇", "repair_status", "已经修好", "还没修", "objective_world"),
    Concept("fridge.state", "refrigerator_door", "冰箱门", "state", "已经关严", "还开着"),
    Concept("towel.location", "clean_towel", "干净毛巾", "location", "浴室柜上层", "阳台架子"),
    Concept("notebook.location", "blue_notebook", "蓝色笔记本", "location", "床边小桌", "书包里"),
    Concept("appointment.time", "protagonist", "你的理发预约", "appointment_time", "星期四下午五点", "星期五上午十点", "protagonist_plan"),
    Concept("takeout.status", "takeout_order", "粥铺外卖", "delivery_status", "骑手取餐中", "已经到门口"),
    Concept("heater.state", "water_heater", "热水器", "state", "已经开启", "还关着"),
    Concept("balcony_door.state", "balcony_door", "阳台门", "state", "已经锁好", "还没锁"),
    Concept("fruit.location", "washed_fruit", "洗好的水果", "location", "餐桌果盘", "冰箱冷藏层"),
)

SEALED_CONCEPTS = (
    Concept("sealed.glasses.location", "reading_glasses", "眼镜", "location", "窗边矮柜", "枕头旁"),
    Concept("sealed.oven.state", "small_oven", "小烤箱", "state", "预热中", "已经关机"),
    Concept("sealed.bus.time", "protagonist", "你的公交车", "departure_time", "晚上六点四十", "晚上六点十分", "protagonist_plan"),
    Concept("sealed.jacket.location", "rain_jacket", "雨衣", "location", "玄关柜顶层", "浴室门后"),
    Concept("sealed.delivery.status", "grocery_order", "生鲜订单", "delivery_status", "正在分拣", "已经送达"),
)

ACTOR_NAMES = (
    "小林", "阿宁", "陈姐", "许舟", "小周", "林叔", "苏晴", "顾远", "阿岚", "小满",
    "程野", "叶青", "罗姐", "沈川", "唐宁", "陆遥", "小乔", "方叔", "安然", "江澄",
    "季禾", "温言", "宋姐", "贺明", "秦川", "夏宁", "周岚", "顾姐", "林舟", "白榆",
)
SEALED_ACTOR_NAMES = ("赵医生", "钱师傅", "孙阿姨", "李店长", "周老师")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"'))


def _base_model_view(index: int) -> dict[str, Any]:
    suffix = f"v5-{index:04d}"
    return {
        "mode": "JUDGE_TURN",
        "evidence_contract": {"allowed_evidence_refs": [f"snap-{suffix}", f"evt-{suffix}"]},
        "snapshot": {
            "snapshot_id": f"snap-{suffix}",
            "request_id": f"req-{suffix}",
            "job_id": None,
            "save_id": "save-v5-pilot",
            "world_id": "world-songjiang",
            "protagonist_id": "protagonist",
            "active_character_id": "baiweixi",
            "captured_game_time": "2026-08-30T18:30:00.000000+08:00",
            "live_world_version": 20 + index,
            "mind_state_version": 8,
            "world_state": {
                "live_world_version": 20 + index,
                "scene_text": "傍晚，两人在出租屋客厅说话。",
                "protagonist_text": "男主正在询问一件日常事情。",
            },
            "protagonist": {
                "protagonist_id": "protagonist",
                "location_id": "rented_home.living_room",
                "location_label": "出租屋客厅",
                "activity": "talking",
                "body_state": {},
                "held_item_ids": [],
            },
            "scene": {
                "scene_id": "rented_home.living_room",
                "location_label": "出租屋客厅",
                "present_character_ids": ["protagonist", "baiweixi"],
                "item_states": {},
            },
        },
        "previous_heroine_runtime": {
            "save_id": "save-v5-pilot",
            "world_id": "world-songjiang",
            "character_id": "baiweixi",
            "version": 8,
            "living_mind": {
                "form": "人形",
                "body": "正常",
                "emotion": "平静",
                "attention": "男主的问题",
                "current_activity": "聊天",
                "immediate_intent": "直接回答男主",
            },
            "relationship": {
                "protagonist_id": "protagonist",
                "stage": "close_companions",
                "trust": "high",
                "unresolved_tension": "",
            },
            "motive_state": {},
            "knowledge_state": {},
            "evidence_refs": [],
            "projection_counts": {"motive_state": 0, "knowledge_state": 0, "evidence_refs": 0},
        },
        "selected_memory_frame": {
            "selector_version": "memory-selector-v1",
            "source_memory_ids": [],
            "source_event_ids": [],
            "selected_memories": [],
        },
        "current_protagonist_utterance": "这件事现在是什么情况？",
        "recent_dialogue": [],
        "pending_actions": [],
        "game_feedback": [],
    }


def _fact(fid: str, concept: Concept, value: str, source: str, status: str) -> dict[str, str]:
    return {
        "fact_id": fid,
        "subject": concept.subject,
        "predicate": concept.predicate,
        "object": value,
        "domain": concept.domain,
        "source": source,
        "status": status,
    }


def _subject_statement(concept: Concept) -> str:
    if concept.predicate == "location":
        return f"{concept.label}在{concept.current}"
    if concept.predicate == "state":
        return f"{concept.label}{concept.current}"
    if concept.predicate.endswith("_time"):
        return f"{concept.label}的时间是{concept.current}"
    if concept.predicate.endswith("_preference"):
        return f"{concept.label}的偏好是{concept.current}"
    return f"{concept.label}的当前状态是{concept.current}"


def _player_label(concept: Concept) -> str:
    if concept.subject == "protagonist" and concept.label.startswith("你的"):
        return "我的" + concept.label[2:]
    return concept.label


def _world_label(concept: Concept) -> str:
    if concept.subject == "protagonist" and concept.label.startswith("你的"):
        return "男主的" + concept.label[2:]
    return concept.label


def _assertion(fact: dict[str, str]) -> dict[str, Any]:
    return {
        "subject": fact["subject"],
        "predicate": fact["predicate"],
        "object": fact["object"],
        "source_fact_ids": [fact["fact_id"]],
    }


def _memory(view: dict[str, Any], concept: Concept, index: int) -> None:
    mid = f"mem-v5-{index:04d}"
    eid = f"evt-mem-v5-{index:04d}"
    view["selected_memory_frame"] = {
        "selector_version": "memory-selector-v1",
        "source_memory_ids": [mid],
        "source_event_ids": [eid],
        "selected_memories": [
            {
                "memory_id": mid,
                "memory_version": 1,
                "kind": "episodic",
                "statement": f"此前{_world_label(concept)}是{concept.stale}。",
                "subject_type": "object" if concept.subject != "protagonist" else "protagonist",
                "subject_display_name": _world_label(concept),
                "temporal_relation": "past",
                "temporal_source_text": "此前",
                "epistemic_polarity": "positive",
                "epistemic_modality": "observed",
                "evidence": [{"event_id": eid, "role": "observation", "excerpt": f"{_world_label(concept)}是{concept.stale}"}],
            }
        ],
    }
    view["evidence_contract"]["allowed_evidence_refs"].extend([mid, eid])


def _scenario(concept: Concept, task: str, index: int, *, sealed: bool) -> dict[str, Any]:
    view = _base_model_view(index)
    current_id = f"fact.{concept.key.replace('.', '_')}.current"
    stale_id = f"fact.{concept.key.replace('.', '_')}.stale"
    current_source = "game_feedback"
    current_fact = _fact(current_id, concept, concept.current, current_source, "current")
    stale_fact = _fact(stale_id, concept, concept.stale, "recent_dialogue", "stale")
    direction = {
        "reply_accept_authoritative_update": ("accept_authoritative_update", "acknowledge_update_then_restated_fact"),
        "reply_reject_stale_claim": ("reject_stale_claim", "explicit_no_then_current_fact"),
        "reply_correct_false_premise": ("correct_false_premise", "correct_premise_then_fact"),
        "reply_resolve_world_memory_conflict": ("prefer_world_over_memory", "prefer_current_world_then_fact"),
        "reply_confirm_current_state": ("confirm_current_state", "explicit_confirmation_then_fact"),
        "reply_subject_attribution": ("preserve_subject", "answer_with_original_subject"),
        "reply_insufficient_information": ("ask_for_clarification", "state_unknown_then_clarify"),
        "reply_direct_answer": ("answer_directly", "direct_answer_then_optional_context"),
    }[task]
    facts: list[dict[str, str]]
    forbidden: list[dict[str, Any]] = []
    player_label = _player_label(concept)
    world_label = _world_label(concept)
    if task == "reply_accept_authoritative_update":
        current_fact["source"] = "current_protagonist_utterance"
        view["current_protagonist_utterance"] = f"我刚确认过，{player_label}现在是{concept.current}，不是{concept.stale}。"
        view["recent_dialogue"] = [f"男主：之前{player_label}是{concept.stale}。", "白未晞：我记着。"]
        facts = [current_fact, stale_fact]
        forbidden = [_assertion(stale_fact)]
    elif task == "reply_reject_stale_claim":
        stale_fact["source"] = "current_protagonist_utterance"
        view["current_protagonist_utterance"] = f"所以{player_label}现在还是{concept.stale}，对吗？"
        view["game_feedback"] = [f"当前状态：{world_label}是{concept.current}；{concept.stale}已经失效。"]
        facts = [current_fact, stale_fact]
        forbidden = [_assertion(stale_fact)]
    elif task == "reply_correct_false_premise":
        stale_fact["source"] = "current_protagonist_utterance"
        stale_fact["status"] = "false"
        view["current_protagonist_utterance"] = f"{player_label}已经是{concept.stale}了，你按这个处理吧。"
        view["game_feedback"] = [f"当前状态：{world_label}是{concept.current}，并非{concept.stale}。"]
        facts = [current_fact, stale_fact]
        forbidden = [_assertion(stale_fact)]
    elif task == "reply_resolve_world_memory_conflict":
        stale_fact["source"] = "selected_memory_frame"
        view["current_protagonist_utterance"] = f"{player_label}现在到底是{concept.current}还是{concept.stale}？"
        view["game_feedback"] = [f"当前状态：{world_label}是{concept.current}。"]
        _memory(view, concept, index)
        facts = [current_fact, stale_fact]
        forbidden = [_assertion(stale_fact)]
    elif task == "reply_confirm_current_state":
        view["current_protagonist_utterance"] = f"{player_label}现在是{concept.current}，对吧？"
        view["game_feedback"] = [f"当前状态：{world_label}是{concept.current}。"]
        facts = [current_fact]
    elif task == "reply_subject_attribution":
        actor_name = (
            SEALED_ACTOR_NAMES[index % len(SEALED_ACTOR_NAMES)]
            if sealed
            else ACTOR_NAMES[index % len(ACTOR_NAMES)]
        )
        statement = _subject_statement(concept)
        actor_fact = {
            **current_fact,
            "fact_id": f"fact.actor_{index:02d}.statement_content",
            "source": "recent_dialogue",
        }
        wrong_subject = "baiweixi" if actor_fact["subject"] == "protagonist" else "protagonist"
        wrong = {**actor_fact, "subject": wrong_subject}
        view["current_protagonist_utterance"] = f"刚才是谁说{statement}？"
        view["recent_dialogue"] = [f"{actor_name}：{statement}。"]
        facts = [actor_fact]
        current_fact = actor_fact
        forbidden = [_assertion(wrong)]
    elif task == "reply_insufficient_information":
        unknown = _fact(current_id, concept, "", "selected_memory_frame", "unknown")
        view["current_protagonist_utterance"] = f"上一次{player_label}变成现在这样，具体是哪一天？"
        facts = [unknown]
        current_fact = unknown
    else:
        view["current_protagonist_utterance"] = f"{player_label}现在是什么情况？"
        view["game_feedback"] = [f"当前状态：{world_label}是{concept.current}。"]
        facts = [current_fact]
    prefix = "sealed" if sealed else "pilot"
    return {
        "schema_version": "aip.runtime_grounded_reply_scenario.v1",
        "scenario_id": f"{prefix}.{TASK_SLUG[task]}.{concept.key}",
        "profile_id": "baiweixi",
        "task_type": task,
        "model_view": view,
        "oracle_view": {
            "facts": facts,
            "correction_direction": direction[0],
            "answer_shape": direction[1],
            "required_assertions": [_assertion(current_fact)],
            "forbidden_assertions": forbidden,
            "must_not_be_vague": True,
            "response_contract": {
                "unknown_acknowledgement": (
                    "required" if task == "reply_insufficient_information" else "not_applicable"
                ),
                "subject_proposition_binding": (
                    "required" if task == "reply_subject_attribution" else "not_applicable"
                ),
                "unsupported_expansion_policy": "reject",
            },
            "unsupported_detail_policy": "reject",
        },
        "split_anchors": [f"task:{task}", f"family:{prefix}.{concept.key}", f"predicate:{current_fact['predicate']}"],
    }


def build_scenarios(concepts: Iterable[Concept], *, sealed: bool) -> list[dict[str, Any]]:
    rows = [
        _scenario(concept, task, index + (1000 if sealed else 0), sealed=sealed)
        for index, concept in enumerate(concepts)
        for task in TASKS
    ]
    validate_scenario_collection(rows)
    return rows


class _OllamaJSONModelAdapter:
    def __init__(self, base_url: str, model_name: str) -> None:
        native_url = base_url.rstrip("/")
        if native_url.endswith("/v1"):
            native_url = native_url[:-3]
        self._client = httpx.Client(base_url=native_url, timeout=60.0)
        self._model_name = model_name

    def generate(self, spec: dict[str, Any]) -> dict[str, Any]:
        messages = list(spec["messages"])
        if "qwen3.5" in self._model_name.lower() and not any(
            message.get("role") == "user" for message in messages
        ):
            messages.append({"role": "user", "content": "请严格按上述要求输出结果。"})
        options: dict[str, Any] = {
            "num_ctx": 8192,
            "num_predict": int(spec.get("max_tokens", 256)),
            "temperature": float(spec.get("temperature", 0.0)),
        }
        seed = spec.get("seed")
        if isinstance(seed, int):
            options["seed"] = seed
        payload: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "stream": False,
            "format": "json",
            "keep_alive": "10m",
            "options": options,
        }
        if "qwen3.5" in self._model_name.lower() or "gemma4" in self._model_name.lower():
            payload["think"] = False
        response = self._client.post("/api/chat", json=payload)
        if response.is_error:
            raise RuntimeError(
                f"Ollama native call failed: HTTP {response.status_code}: {response.text[:500]}"
            )
        payload = response.json()
        content = str((payload.get("message") or {}).get("content", ""))
        if not content.strip():
            raise RuntimeError("Ollama native JSON call returned empty content")
        return {"content": content, "finish_reason": str(payload.get("done_reason", ""))}


class _DirectExecutor:
    def __init__(self, model: Any, model_name: str) -> None:
        self._model = model
        self._model_name = model_name
        self._ids: list[str] = []

    def call(self, spec: dict[str, Any], **metadata: Any) -> dict[str, Any]:
        stage = str(metadata.get("stage", "model_call"))
        started = time.monotonic()
        print(f"[model] start {stage}", flush=True)
        result = self._model.generate({**spec, "model": self._model_name})
        print(f"[model] done {stage} {time.monotonic() - started:.2f}s", flush=True)
        raw = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self._ids.append("sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest())
        return result

    def record_ids(self) -> list[str]:
        return list(self._ids)


def _package(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "profile": {"profile_id": "baiweixi", "display_name": "白未晞"},
        "protocol": {"reply_runtime_rules": ["只输出说出口的自然对白，不写动作旁白。"]},
        "runtime_grounded_scenarios": {row["scenario_id"]: row for row in scenarios},
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    for attempt in range(5):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


def _parse_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.I | re.S)
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response does not contain a JSON object")
    value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("response JSON is not an object")
    return value


def generate_grounded(
    scenarios: list[dict[str, Any]], output: Path, *, base_url: str, model_name: str,
    workers: int, max_attempts: int = 2,
) -> list[dict[str, Any]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    existing: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(output):
        scenario = row.get("scenario") or {}
        sid = scenario.get("scenario_id")
        try:
            report = evaluate_runtime_grounded_candidate(
                scenario,
                row.get("teacher_target") or {},
                row.get("semantic_audit") or {},
            )
        except Exception as error:  # noqa: BLE001
            print(f"[grounded] drop stale candidate {sid}: {error}", flush=True)
            continue
        if not report.approved:
            print(
                f"[grounded] drop rejected candidate {sid}: {report.to_dict()}",
                flush=True,
            )
            continue
        existing[str(sid)] = row
    package = _package(scenarios)
    model = _OllamaJSONModelAdapter(base_url, model_name)
    adapter = RuntimeGroundedReplyAdapter(
        teacher_adapter_id="v5-local",
        audit_adapter_id="v5-local",
        teacher_model=model_name,
        audit_model=model_name,
        teacher_max_tokens=220 if "27b" in model_name.lower() else 160,
        audit_max_tokens=450,
        teacher_temperature=0.0,
        teacher_retry_temperature=0.2,
    )

    def run(scenario: dict[str, Any]) -> dict[str, Any]:
        sid = scenario["scenario_id"]
        scenario_seed = (20260830 ^ (zlib.crc32(sid.encode("utf-8")) & 0x7FFFFFFF)) & 0x7FFFFFFF
        last_failure: dict[str, str] | None = None
        for attempt in range(1, max_attempts + 1):
            executor = _DirectExecutor(model, model_name)
            item = {
                "fixture_id": sid,
                "attempt_no": attempt,
                "candidate_no": 1,
                "seed": scenario_seed + attempt - 1,
            }
            if last_failure is not None:
                item["_last_failure"] = last_failure
            try:
                result = adapter.generate(adapter.prepare(item, package), executor)
            except Exception as error:  # noqa: BLE001
                result = {
                    "mode_failure": True,
                    "error_code": "provider_error",
                    "reason": str(error),
                }
            if not result.get("mode_failure"):
                target = result["target"]
                return {
                    "sample_id": sid,
                    "scenario": scenario,
                    "teacher_target": target["teacher_target"],
                    "semantic_audit": target["semantic_audit"],
                    "gate_report": target["runtime_grounded_gate_report"],
                    "provenance": {
                        "teacher_model": model_name,
                        "audit_model": model_name,
                        "teacher_and_audit_are_separate_calls": True,
                        "context_window": 8192,
                        "model_transport": "ollama_native_api_chat_json",
                        "attempt": attempt,
                        "seed": item["seed"],
                        "call_hashes": executor.record_ids(),
                        "generated_at": _now(),
                    },
                }
            last_failure = {
                "error_code": str(result.get("error_code", "")),
                "reason": str(result.get("reason", "")),
            }
        raise RuntimeError(
            f"grounded scenario exhausted {max_attempts} attempts: {sid}: "
            f"{result.get('reason')}"
        )

    pending = [row for row in scenarios if row["scenario_id"] not in existing]
    lock = Lock()
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, row): row["scenario_id"] for row in pending}
        for future in as_completed(futures):
            sid = futures[future]
            try:
                row = future.result()
            except Exception as error:  # noqa: BLE001
                failures.append(f"{sid}: {error}")
                print(f"[grounded] failed {sid}: {error}", flush=True)
                continue
            with lock:
                existing[row["scenario"]["scenario_id"]] = row
                ordered = [existing[s["scenario_id"]] for s in scenarios if s["scenario_id"] in existing]
                _write_jsonl(output, ordered)
                print(f"[grounded] {len(existing)}/{len(scenarios)} {row['scenario']['scenario_id']}", flush=True)
    if failures:
        raise RuntimeError("grounded generation failures:\n" + "\n".join(failures))
    return [existing[row["scenario_id"]] for row in scenarios]


STATIC_REVIEW_PROMPT = """你是白未晞静态人设训练样本的独立 V5 复审员。system 消息是本样本的权威角色正典，不能把其中已声明的伤势、猫妖身份、妖力、做饭能力、关系和生活经历误判为杜撰。逐轮审核所有女主回复。

只有出现以下硬问题才令 approved=false：回答与紧邻玩家话语明显不相关或前后逻辑断裂；主体/指代明显错位；杜撰 system 与对话都未提供的具体共同经历；用 *动作*、括号或叙述句写动作旁白；Markdown、系统解释、助手腔；明显违反 system 正典。

省略号、感官表达、简短关心、轻微主动、自然口语或不影响含义的风格差异都允许，不能单独作为拒绝理由。只输出 JSON：{\"approved\":true|false,\"severe_fact_or_subject_error\":true|false,\"reasons\":[\"...\"]}。"""


def review_static(
    data_path: Path,
    metadata_path: Path,
    output_records: Path,
    output_audits: Path,
    *,
    base_url: str,
    model_name: str,
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = _read_jsonl(data_path)
    metadata_rows = _read_jsonl(metadata_path)
    if len(data) != len(metadata_rows):
        raise RuntimeError(f"static data/metadata line mismatch: {len(data)} != {len(metadata_rows)}")
    metadata = {row["sample_id"]: row for row in metadata_rows}
    existing = {row["sample_id"]: row for row in _read_jsonl(output_audits)}
    candidates: list[dict[str, Any]] = []
    for row, line_meta in zip(data, metadata_rows, strict=True):
        sid = str(line_meta.get("sample_id", ""))
        meta = metadata.get(sid) or {}
        if meta.get("task_type") == "reply_correction":
            continue
        conversations = row.get("conversations") or []
        if not conversations or conversations[-1].get("from") not in {"gpt", "assistant"}:
            continue
        candidates.append({**row, "sample_id": sid, "task_type": meta.get("task_type", "static_persona_reply")})

    model = _OllamaJSONModelAdapter(base_url, model_name)

    def run(row: dict[str, Any]) -> dict[str, Any]:
        sid = row["sample_id"]
        conversation = row["conversations"]
        spec = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": STATIC_REVIEW_PROMPT},
                {"role": "user", "content": json.dumps(conversation, ensure_ascii=False)},
            ],
            "temperature": 0.0,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
            "extra_body": {"options": {"num_ctx": 8192}},
            "api_key_env": "V5_LOCAL_API_KEY",
        }
        for attempt in (1, 2):
            try:
                result = _parse_object(model.generate(spec)["content"])
                approved = result.get("approved") is True and result.get("severe_fact_or_subject_error") is False
                return {
                    "sample_id": sid,
                    "approved": approved,
                    "audit_version": "runtime-grounded-static-v1",
                    "split_anchor_ids": (metadata.get(sid) or {}).get("split_anchor_ids") or [f"static:{sid}"],
                    "reviewer_type": "independent_model_review",
                    "reviewer_model": model_name,
                    "severe_fact_or_subject_error": bool(result.get("severe_fact_or_subject_error")),
                    "reasons": [str(value) for value in result.get("reasons", [])],
                    "reviewed_at": _now(),
                    "attempt": attempt,
                }
            except Exception as error:  # noqa: BLE001
                last = error
        return {
            "sample_id": sid,
            "approved": False,
            "audit_version": "runtime-grounded-static-v1",
            "split_anchor_ids": (metadata.get(sid) or {}).get("split_anchor_ids") or [f"static:{sid}"],
            "reviewer_type": "independent_model_review",
            "reviewer_model": model_name,
            "severe_fact_or_subject_error": False,
            "reasons": [f"audit_parse_failed:{last}"],
            "reviewed_at": _now(),
            "attempt": 2,
        }

    pending = [row for row in candidates if row["sample_id"] not in existing]
    selected_ids: list[str] = []
    for row in candidates:
        audit = existing.get(row["sample_id"])
        if audit and audit.get("approved"):
            selected_ids.append(row["sample_id"])
        if len(selected_ids) >= 160:
            break
    if len(selected_ids) < 160:
        for offset in range(0, len(pending), 30):
            batch = pending[offset : offset + 30]
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(run, row): row for row in batch}
                for future in as_completed(futures):
                    audit = future.result()
                    existing[audit["sample_id"]] = audit
                    _write_jsonl(output_audits, existing.values())
                    approved_count = sum(value.get("approved") is True for value in existing.values())
                    print(f"[static] reviewed={len(existing)} approved={approved_count}", flush=True)
            if approved_count >= 175:
                break
    selected: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for row in candidates:
        audit = existing.get(row["sample_id"])
        if audit and audit.get("approved"):
            selected.append(row)
            audits.append(audit)
        if len(selected) == 160:
            break
    if len(selected) != 160:
        raise RuntimeError(f"static independent review produced only {len(selected)} approved samples")
    _write_jsonl(output_records, selected)
    _write_jsonl(output_audits, audits)
    return selected, audits


def _reference_reply(scenario: dict[str, Any]) -> str:
    oracle = scenario["oracle_view"]
    assertion = oracle["required_assertions"][0]
    value = assertion["object"]
    predicate = assertion["predicate"]
    direction = oracle["correction_direction"]
    if direction == "ask_for_clarification":
        return "这个具体时间我没有可靠记录，不能确定。你还记得别的线索吗？"
    if direction == "preserve_subject":
        dialogue = scenario["model_view"].get("recent_dialogue") or []
        speaker = dialogue[0].split("：", 1)[0].strip() if dialogue and "：" in dialogue[0] else ""
        statement = dialogue[0].split("：", 1)[1].strip().rstrip("。！？") if speaker else ""
        if not speaker:
            raise RuntimeError(f"sealed 主体归属场景缺具体说话者: {scenario['scenario_id']}")
        return f"是{speaker}说的，他说{statement}。"
    value_phrase = f"在{value}" if predicate == "location" else f"是{value}"
    if direction in {"reject_stale_claim", "correct_false_premise"}:
        return f"不是，当前{value_phrase}。"
    if direction == "confirm_current_state":
        return f"对，现在{value_phrase}。"
    if direction == "accept_authoritative_update":
        return f"知道了，我记成{value_phrase}。"
    if direction == "prefer_world_over_memory":
        return f"按现在的状态，{value_phrase}。"
    return f"现在{value_phrase}。"


def build_sealed_rows(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        reply = _reference_reply(scenario)
        record = TrainingRecordV4(
            sample_id=scenario["scenario_id"],
            mode="RUNTIME_GROUNDED_REPLY",
            render_profile_id="runtime-grounded-reply-v1",
            messages=[
                {"role": "human", "content": json.dumps(scenario["model_view"], ensure_ascii=False, sort_keys=True)},
                {"role": "assistant", "content": reply},
            ],
            supervised_message_indexes=[1],
            supervised_token_count=estimate_token_count(
                [
                    {"role": "human", "content": json.dumps(scenario["model_view"], ensure_ascii=False, sort_keys=True)},
                    {"role": "assistant", "content": reply},
                ],
                [1],
            ),
            protocol_snapshot_id="runtime-grounded-sealed-v1",
            system_anchor="你是白未晞。只输出说出口的自然回复。",
        )
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "task_type": scenario["task_type"],
                "split_anchors": scenario["split_anchors"],
                "training_record": record.to_dict(),
                "oracle_view": scenario["oracle_view"],
            }
        )
    return rows


def inspect_grounded(rows: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    for task in TASKS:
        task_rows = [row for row in rows if row["scenario"]["task_type"] == task]
        ordered = sorted(task_rows, key=lambda row: hashlib.sha256(row["sample_id"].encode()).hexdigest())
        selected.extend(ordered[:10])
    reviews: list[dict[str, Any]] = []
    severe = 0
    for row in selected:
        scenario = row["scenario"]
        teacher = row["teacher_target"]
        audit = row["semantic_audit"]
        report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
        subject_error = report.verdicts.get("RG7") is not True
        severe_error = not report.approved or subject_error
        severe += int(severe_error)
        reviews.append(
            {
                "sample_id": row["sample_id"],
                "task_type": scenario["task_type"],
                "reply": teacher["reply"],
                "approved": not severe_error,
                "severe_fact_error": not report.approved,
                "subject_error": subject_error,
                "gate_report": report.to_dict(),
                "reviewer_type": "codex_deterministic_secondary_inspection",
            }
        )
    result = {
        "schema_version": "aip.runtime_grounded_spot_check.v1",
        "reviewer_type": "codex_deterministic_secondary_inspection",
        "human_review_completed": False,
        "selection": "sha256 order, 10 per task",
        "total": len(reviews),
        "severe_errors": severe,
        "passed": severe == 0 and len(reviews) == 80,
        "reviews": reviews,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result["passed"]:
        raise RuntimeError(f"80-row secondary inspection failed with {severe} severe errors")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "训练数据" / "baiweixi_v5_pilot_v1")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    _load_env(ROOT / ".env")
    os.environ.setdefault("V5_LOCAL_API_KEY", "EMPTY")
    inputs = args.output_root / "inputs"
    package_dir = args.output_root / "package"
    inputs.mkdir(parents=True, exist_ok=True)

    grounded_scenarios = build_scenarios(TRAIN_CONCEPTS, sealed=False)
    sealed_scenarios = build_scenarios(SEALED_CONCEPTS, sealed=True)
    _write_jsonl(inputs / "grounded_scenarios.jsonl", grounded_scenarios)
    _write_jsonl(inputs / "sealed_scenarios.jsonl", sealed_scenarios)
    grounded = generate_grounded(
        grounded_scenarios,
        inputs / "grounded_candidates.jsonl",
        base_url=args.base_url,
        model_name=args.model,
        workers=args.workers,
    )
    static, static_audits = review_static(
        ROOT / "训练数据" / "baiweixi_v4_final.jsonl",
        ROOT / "训练数据" / "baiweixi_v4_final.metadata.jsonl",
        inputs / "static_records.jsonl",
        inputs / "static_reaudit.jsonl",
        base_url=args.base_url,
        model_name=args.model,
        workers=args.workers,
    )
    sealed = build_sealed_rows(sealed_scenarios)
    _write_jsonl(inputs / "sealed.jsonl", sealed)
    inspection = inspect_grounded(grounded, inputs / "grounded_spot_check_80.json")
    report = build_pilot_package(
        grounded_candidates=grounded,
        static_records=static,
        static_reaudit=static_audits,
        sealed_rows=sealed,
        output_dir=package_dir,
    )
    manifest = {
        "schema_version": "aip.runtime_grounded_real_input_manifest.v1",
        "created_at": _now(),
        "teacher_model": args.model,
        "audit_model": args.model,
        "model_endpoint": args.base_url,
        "teacher_and_audit_are_separate_calls": True,
        "human_review_completed": False,
        "codex_secondary_inspection": {"total": inspection["total"], "severe_errors": inspection["severe_errors"]},
        "counts": report.to_dict(),
        "files": {},
        "training_started": False,
    }
    for path in sorted(args.output_root.rglob("*")):
        if path.is_file() and path.name != "input_manifest.json":
            manifest["files"][str(path.relative_to(args.output_root)).replace("\\", "/")] = _sha256(path)
    (args.output_root / "input_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output_root), "counts": report.to_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
