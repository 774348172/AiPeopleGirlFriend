from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.adapters import GenerationOptions, OllamaConfig, OllamaWorldMindBackend
from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ActiveSceneState,
    GameClockService,
    InMemoryWorldStateProvider,
    LlamaCppWorldMindModel,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindModelIdentity,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.model_gateway import (
    GAME_REPLY,
    MindAdvanceResult,
    MindPatchV2Request,
    MindPatchV2Result,
)
from runtime.world_mind.short_protocol import _snapshot_assertions
from runtime.world_mind.state import HeroineMindPatch


INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)
MODEL_MODES = (
    "FOREGROUND_SEMANTIC_TURN",
    "MIND_PATCH_V2",
    "TURN_MIND_ADVANCE",
    "WORLD_CONTINUITY_REVIEW",
    "GAME_REPLY",
    "POST_REPLY_WORLD_MIND_RECONCILE",
    "FIVE_MINUTE_WORLD_MIND_RECONCILE",
)

PLAYER_TURNS = (
    {
        "text": "今天雨下得有点大。",
        "activity": "坐在餐桌旁休息",
        "scene": {"rain_window": "窗外一直在下雨"},
    },
    {
        "text": "窗户关好了吗？",
        "activity": "看着窗户",
        "scene": {"rain_window": "窗外一直在下雨，窗户已经关好"},
    },
    {
        "text": "你伤口今天还疼吗？",
        "activity": "坐在餐桌旁休息",
        "scene": {"rain_window": "窗外一直在下雨，窗户已经关好"},
    },
    {
        "text": "桌上的水是你倒的吗？",
        "activity": "拿起桌上的水杯",
        "scene": {
            "rain_window": "窗外一直在下雨，窗户已经关好",
            "water_cup": "桌上放着一杯水",
        },
    },
    {
        "text": "纸箱要不要换个地方？",
        "activity": "看向墙边的纸箱",
        "scene": {
            "rain_window": "窗外一直在下雨，窗户已经关好",
            "cardboard_box": "白未晞常待的纸箱仍在墙边",
        },
    },
    {
        "text": "晚饭你想吃什么？",
        "activity": "准备点晚饭",
        "scene": {"delivery_status": "尚未下单"},
    },
    {
        "text": "我点了糖醋排骨和青菜。",
        "activity": "刚刚完成游戏内外卖下单",
        "scene": {"delivery_status": "已经下单，正在等待商家制作"},
    },
    {
        "text": "外卖还得四十分钟才到。",
        "activity": "查看游戏内外卖进度",
        "scene": {"delivery_status": "预计四十分钟后送达，当前尚未送达"},
    },
    {
        "text": "对了，你觉得明天还会下雨吗？",
        "activity": "看向窗外",
        "scene": {
            "rain_window": "窗外仍在下雨",
            "delivery_status": "预计四十分钟后送达，当前尚未送达",
        },
    },
    {
        "text": "我刚看了一眼，外卖显示已经到门口了，你去端一下。",
        "activity": "查看游戏内外卖进度",
        "scene": {"delivery_status": "应用暂时显示已经送达门口"},
    },
    {
        "text": "等等，我看错了，外卖还没有到，你端什么？",
        "activity": "重新确认游戏内外卖进度",
        "scene": {
            "delivery_status": "尚未送达；男主已经确认上一条送达提示是看错了"
        },
    },
    {
        "text": "那你现在知道外卖到底到了没有？",
        "activity": "等待白未晞确认当前状态",
        "scene": {"delivery_status": "尚未送达"},
    },
)


class RecordingSeedBackend:
    def __init__(self, backend: OllamaWorldMindBackend, *, seed_base: int) -> None:
        self.backend = backend
        self.seed_base = seed_base
        self.calls: list[dict[str, Any]] = []

    async def start(self) -> None:
        await self.backend.start()

    async def close(self) -> None:
        await self.backend.close()

    async def complete_chat(
        self,
        *,
        request_id: str,
        messages: Sequence[Mapping[str, str]],
        options: GenerationOptions,
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        turn_match = re.search(r"turn-(\d+)", request_id)
        turn = int(turn_match.group(1)) if turn_match else 0
        seeded_options = replace(options, seed=self.seed_base + turn)
        mode = next(
            (item for item in MODEL_MODES if f":{item}:" in request_id),
            "UNKNOWN",
        )
        record: dict[str, Any] = {
            "request_id": request_id,
            "mode": mode,
            "seed": seeded_options.seed,
            "options": {
                "max_tokens": seeded_options.max_tokens,
                "temperature": seeded_options.temperature,
                "top_p": seeded_options.top_p,
                "repeat_penalty": seeded_options.repeat_penalty,
            },
            "messages": [dict(item) for item in messages],
            "has_response_format": response_format is not None,
        }
        started = time.perf_counter()
        try:
            response = await self.backend.complete_chat(
                request_id=request_id,
                messages=messages,
                options=seeded_options,
                response_format=response_format,
            )
            record["ok"] = True
            record["response"] = response
            return response
        except BaseException as error:
            record["ok"] = False
            record["error_type"] = type(error).__name__
            record["error"] = str(error)
            raise
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
            self.calls.append(record)


class FrozenMindWorldMindModel(LlamaCppWorldMindModel):
    async def propose_mind_patch_v2(
        self, request: MindPatchV2Request
    ) -> MindPatchV2Result:
        snapshot = request.snapshot
        return MindPatchV2Result(
            mind_result=MindAdvanceResult(
                patch=HeroineMindPatch(evidence_refs=(snapshot.snapshot_id,)),
                reply_intent="从当前状态回应男主",
                fact_assertions=_snapshot_assertions(snapshot),
                parent_mind_state_version=snapshot.mind_state_version,
                snapshot_id=snapshot.snapshot_id,
                transition_basis=(snapshot.snapshot_id,),
            ),
            changed_field_codes=(),
            raw_output={"diagnostic_control": "frozen_mind"},
        )


def _runtime_config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
        foreground_protocol="mind_patch_v2",
    )


def _session(model_id: str) -> RuntimeSessionIdentity:
    suffix = re.sub(r"[^a-z0-9_]", "_", model_id.lower()).strip("_")
    return RuntimeSessionIdentity(
        save_id=f"multiturn_ab_{suffix}",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=f"multiturn_ab_{suffix}",
    )


def _world(turn: Mapping[str, object]) -> tuple[ProtagonistLiveState, ActiveSceneState]:
    return (
        ProtagonistLiveState(
            protagonist_id="protagonist",
            location_id="apartment_living_room",
            location_label="出租屋客厅",
            activity=str(turn["activity"]),
            body_state={"fatigue": "轻微疲惫", "injury": "无"},
            held_item_ids=(),
        ),
        ActiveSceneState(
            scene_id="apartment_living_room",
            location_label="出租屋客厅",
            present_character_ids=("protagonist", "baiweixi"),
            item_states={str(key): str(value) for key, value in turn["scene"].items()},
        ),
    )


async def _model_digest(base_url: str, model_name: str) -> str:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
    aliases = {model_name, f"{model_name}:latest"}
    for item in response.json().get("models", []):
        if isinstance(item, dict) and (
            item.get("name") in aliases or item.get("model") in aliases
        ):
            digest = str(item.get("digest", "")).lower()
            if len(digest) == 64:
                return digest
    raise RuntimeError(f"cannot resolve Ollama digest for {model_name}")


async def _run_model(
    *,
    label: str,
    model_name: str,
    run_dir: Path,
    base_url: str,
    seed_base: int,
    freeze_mind: bool,
) -> dict[str, object]:
    digest = await _model_digest(base_url, model_name)
    database_path = run_dir / f"{label}.sqlite3"
    session = _session(label)
    store = WorldMindStore.open(database_path)
    clock = GameClockService(store, initial_game_time=INITIAL_GAME_TIME)
    provider = InMemoryWorldStateProvider()
    backend = RecordingSeedBackend(
        OllamaWorldMindBackend(
            OllamaConfig(
                model_name=model_name,
                base_url=base_url,
                context_size=4096,
                keep_alive="10m",
                request_timeout_seconds=180.0,
                disable_thinking=True,
                stream_response=False,
                num_gpu=20,
            )
        ),
        seed_base=seed_base,
    )
    model_class = FrozenMindWorldMindModel if freeze_mind else LlamaCppWorldMindModel
    model = model_class(
        backend,
        identity=WorldMindModelIdentity(
            model_id=f"ollama/{model_name}",
            revision=f"ollama-digest:{digest}",
            artifact_sha256=digest,
            character_id="baiweixi",
            world_id="songjiangfu",
            protagonist_id="protagonist",
        ),
    )
    runtime = WorldMindRuntime(
        config=_runtime_config(),
        store=store,
        world_state_provider=provider,
        game_clock=clock,
        model=model,
        periodic_reconcile_seconds=300.0,
        required_reconcile_before_foreground=False,
        coalesce_pending_required_reconcile=True,
    )
    turns: list[dict[str, object]] = []
    try:
        await runtime.start()
        for index, player_turn in enumerate(PLAYER_TURNS, start=1):
            protagonist, scene = _world(player_turn)
            await provider.update_latest(
                session,
                protagonist,
                scene,
                clock.current_time(session),
            )
            call_start = len(backend.calls)
            result = await runtime.handle_turn(
                TurnRequest(
                    request_id=f"ab-{label}-turn-{index:02d}",
                    session=session,
                    text=str(player_turn["text"]),
                )
            )
            turn_calls = backend.calls[call_start:]
            game_call = next(
                (call for call in reversed(turn_calls) if call["mode"] == GAME_REPLY),
                None,
            )
            heroine = store.load_heroine_runtime(session)
            turn_record: dict[str, object] = {
                "turn": index,
                "player": player_turn["text"],
                "world": player_turn["scene"],
                "result_type": type(result).__name__,
                "reply": result.text if isinstance(result, Completed) else None,
                "failure_code": result.code if isinstance(result, Failed) else None,
                "recent_dialogue_after_commit": store.list_recent_dialogue(session),
                "mind_after_commit": (
                    {
                        "version": heroine.version,
                        "attention": heroine.living_mind.attention,
                        "activity": heroine.living_mind.current_activity,
                        "immediate_intent": heroine.living_mind.immediate_intent,
                    }
                    if heroine is not None
                    else None
                ),
                "game_reply_messages": game_call["messages"] if game_call else None,
                "game_reply_seed": game_call["seed"] if game_call else None,
            }
            turns.append(turn_record)
            if not isinstance(result, Completed):
                break
    finally:
        try:
            await runtime.close()
        finally:
            store.close()
    return {
        "label": label,
        "model": model_name,
        "ollama_digest": digest,
        "freeze_mind": freeze_mind,
        "database_path": str(database_path),
        "turns": turns,
        "calls": backend.calls,
    }


async def _run(args: argparse.Namespace) -> int:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / f"base_lora_multiturn_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    report: dict[str, object] = {
        "schema_version": 1,
        "scope": "production_renderer_stateful_multiturn_base_vs_lora_ab",
        "generated_at": datetime.now().astimezone().isoformat(),
        "controls": {
            "foreground_protocol": "mind_patch_v2",
            "context_size": 4096,
            "num_gpu": 20,
            "history_limit_messages": 6,
            "seed_base": args.seed_base,
            "freeze_mind": args.freeze_mind,
            "player_turns": PLAYER_TURNS,
            "memory_retrieval": "empty new-save baseline",
        },
        "models": [],
    }
    exit_code = 0
    for label, model_name in (
        ("base", args.base_model),
        ("lora", args.lora_model),
    ):
        try:
            model_report = await _run_model(
                label=label,
                model_name=model_name,
                run_dir=run_dir,
                base_url=args.base_url,
                seed_base=args.seed_base,
                freeze_mind=args.freeze_mind,
            )
        except BaseException as error:
            model_report = {
                "label": label,
                "model": model_name,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            exit_code = 1
        report["models"].append(model_report)
        summary = {
            "label": label,
            "model": model_name,
            "error": model_report.get("error"),
            "turns": [
                {
                    "turn": turn["turn"],
                    "player": turn["player"],
                    "reply": turn["reply"],
                    "failure_code": turn["failure_code"],
                    "mind_after_commit": turn["mind_after_commit"],
                }
                for turn in model_report.get("turns", [])
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"REPORT_PATH={report_path}")
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a stateful V6 GAME_REPLY multi-turn A/B for base and LoRA models."
    )
    parser.add_argument(
        "--base-model", default="qwen2.5:7b-instruct-q4_K_M"
    )
    parser.add_argument("--lora-model", default="baiweixi-7b-fix:latest")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--seed-base", type=int, default=4200)
    parser.add_argument("--freeze-mind", action="store_true")
    parser.add_argument(
        "--output-root",
        default=str(ROOT / "eval" / "world_mind_p0"),
    )
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(_parser().parse_args())))
