from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

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
    WorldMindModeProfile,
    WorldMindModelIdentity,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    GAME_REPLY,
    POST_REPLY_WORLD_MIND_RECONCILE,
    TURN_MIND_ADVANCE,
    WORLD_CONTINUITY_REVIEW,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL_SHA256 = "433f4f1cee5763af6007ae0ca882d45212ed5efcd0b196f3255af18d42e6c440"
EXPECTED_MODES = {
    TURN_MIND_ADVANCE,
    WORLD_CONTINUITY_REVIEW,
    GAME_REPLY,
    POST_REPLY_WORLD_MIND_RECONCILE,
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
}
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)


class RecordingBackend:
    def __init__(self, backend: OllamaWorldMindBackend) -> None:
        self.backend = backend
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
        payload = json.loads(messages[-1]["content"])
        mode = str(payload.get("mode", "unknown"))
        record: dict[str, Any] = {
            "request_id": request_id,
            "mode": mode,
            "started_at": datetime.now().astimezone().isoformat(),
            "options": {
                "max_tokens": options.max_tokens,
                "temperature": options.temperature,
                "top_p": options.top_p,
                "repeat_penalty": options.repeat_penalty,
                "seed": options.seed,
            },
        }
        started = time.perf_counter()
        try:
            raw = await self.backend.complete_chat(
                request_id=request_id,
                messages=messages,
                options=options,
                response_format=response_format,
            )
            record["ok"] = True
            record["raw_response"] = raw
            return raw
        except BaseException as error:
            record["ok"] = False
            record["error_type"] = type(error).__name__
            record["error"] = str(error)
            raise
        finally:
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            self.calls.append(record)


def _runtime_config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
    )


def _session(run_id: str) -> RuntimeSessionIdentity:
    identifier = run_id.replace("-", "_")
    return RuntimeSessionIdentity(
        save_id=f"qin_probe_{identifier}",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=f"qin_probe_{identifier}",
    )


def _mode_profiles(timeout_seconds: float) -> dict[str, WorldMindModeProfile]:
    return {
        TURN_MIND_ADVANCE: WorldMindModeProfile(
            900, 0.2, 0.85, 1.05, timeout_seconds, 1
        ),
        WORLD_CONTINUITY_REVIEW: WorldMindModeProfile(
            650, 0.1, 0.8, 1.05, timeout_seconds, 1
        ),
        GAME_REPLY: WorldMindModeProfile(
            600, 0.75, 0.9, 1.1, timeout_seconds, 1
        ),
        POST_REPLY_WORLD_MIND_RECONCILE: WorldMindModeProfile(
            320, 0.2, 0.85, 1.05, timeout_seconds, 0
        ),
        FIVE_MINUTE_WORLD_MIND_RECONCILE: WorldMindModeProfile(
            320, 0.2, 0.85, 1.05, timeout_seconds, 0
        ),
    }


def _world_state() -> tuple[ProtagonistLiveState, ActiveSceneState]:
    protagonist = ProtagonistLiveState(
        protagonist_id="protagonist",
        location_id="apartment_table",
        location_label="出租屋餐桌旁",
        activity="吃面",
        body_state={"fatigue": "轻微疲惫", "injury": "无"},
        held_item_ids=("chopsticks",),
    )
    scene = ActiveSceneState(
        scene_id="apartment_table",
        location_label="出租屋餐桌旁",
        present_character_ids=("protagonist", "baiweixi"),
        item_states={
            "noodle_bowl": "放在男主面前，碗里还有面",
            "rain_window": "窗外仍在下雨",
        },
    )
    return protagonist, scene


async def _run(args: argparse.Namespace) -> int:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / f"qinweixi-qwen35-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "report.json"
    database_path = run_dir / "world_mind.sqlite3"
    session = _session(run_id.lower())
    store = WorldMindStore.open(database_path)
    clock = GameClockService(store, initial_game_time=INITIAL_GAME_TIME)
    provider = InMemoryWorldStateProvider()
    ollama = OllamaWorldMindBackend(
        OllamaConfig(
            model_name=args.model,
            base_url=args.base_url,
            context_size=args.context_size,
            keep_alive=args.keep_alive,
            request_timeout_seconds=args.timeout_seconds,
        )
    )
    recording = RecordingBackend(ollama)
    model = LlamaCppWorldMindModel(
        recording,
        identity=WorldMindModelIdentity(
            model_id=f"ollama/{args.model}-legacy-compatibility-probe",
            revision="qin-legacy-v6-engineering-probe-v1",
            artifact_sha256=MODEL_SHA256,
            character_id="baiweixi",
            world_id="songjiangfu",
            protagonist_id="protagonist",
        ),
        mode_profiles=_mode_profiles(args.timeout_seconds),
    )
    runtime = WorldMindRuntime(
        config=_runtime_config(),
        store=store,
        world_state_provider=provider,
        game_clock=clock,
        model=model,
        periodic_reconcile_seconds=300.0,
    )
    report: dict[str, Any] = {
        "test_only": True,
        "acceptance_scope": "V6 engineering compatibility only",
        "not_accepted_as": [
            "白未晞角色语义验收",
            "白未晞正式模型验收",
            "正式存档结果",
        ],
        "model": {
            "ollama_name": args.model,
            "weights_character": "qinweixi_legacy",
            "artifact_sha256": MODEL_SHA256,
            "base_url": args.base_url,
            "context_size": args.context_size,
        },
        "runtime_binding": {
            "prompt_character_id": "baiweixi",
            "world_id": "songjiangfu",
            "protagonist_id": "protagonist",
            "save_id": session.save_id,
            "database_path": str(database_path),
        },
        "started_at": datetime.now().astimezone().isoformat(),
        "turn": {},
        "database": {},
        "calls": recording.calls,
    }
    exit_code = 1
    try:
        await runtime.start()
        protagonist, scene = _world_state()
        await provider.update_latest(
            session,
            protagonist,
            scene,
            clock.current_time(session),
        )
        result = await runtime.handle_turn(
            TurnRequest(
                request_id=f"qin-probe-turn-{run_id.lower()}",
                session=session,
                text="我还在吃面，等吃完再去店里。你现在在做什么？",
            )
        )
        report["turn"] = {
            "result_type": type(result).__name__,
            "text": result.text if isinstance(result, Completed) else None,
            "failure_code": result.code if isinstance(result, Failed) else None,
            "metrics": (
                {
                    "model_total_ms": result.metrics.model_total_ms,
                    "total_ms": result.metrics.total_ms,
                    "output_chars": result.metrics.output_chars,
                }
                if isinstance(result, Completed)
                else None
            ),
        }
        if not isinstance(result, Completed):
            raise RuntimeError(f"turn failed: {result.code}")

        await runtime.wait_background_idle(
            session,
            timeout_seconds=args.background_timeout_seconds,
        )
        await runtime.trigger_periodic_reconcile(session)
        await runtime.wait_background_idle(
            session,
            timeout_seconds=args.background_timeout_seconds,
        )

        heroine = store.load_heroine_runtime(session)
        mode_counts = Counter(call["mode"] for call in recording.calls)
        successful_modes = {
            call["mode"] for call in recording.calls if call.get("ok") is True
        }
        report["database"] = {
            "model_decisions": store.count_model_decisions(session.save_id),
            "reconcile_jobs_total": store.count_reconcile_jobs(session.save_id),
            "reconcile_jobs_completed": store.count_reconcile_jobs(
                session.save_id, state="completed"
            ),
            "reconcile_jobs_failed": store.count_reconcile_jobs(
                session.save_id, state="failed"
            ),
            "reconcile_decisions": store.count_reconcile_decisions(session.save_id),
            "heroine_mind_version": heroine.version if heroine is not None else None,
        }
        report["mode_counts"] = dict(sorted(mode_counts.items()))
        report["missing_modes"] = sorted(EXPECTED_MODES - successful_modes)
        report["passed"] = (
            not report["missing_modes"]
            and report["database"]["model_decisions"] == 1
            and report["database"]["reconcile_jobs_completed"] >= 2
            and report["database"]["reconcile_jobs_failed"] == 0
            and report["database"]["reconcile_decisions"] >= 2
        )
        exit_code = 0 if report["passed"] else 1
    except BaseException as error:
        report["passed"] = False
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
    finally:
        report["finished_at"] = datetime.now().astimezone().isoformat()
        report["calls"] = recording.calls
        try:
            await runtime.close()
        finally:
            store.close()
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        summary = {
            "passed": report.get("passed", False),
            "turn": report.get("turn", {}),
            "database": report.get("database", {}),
            "mode_counts": report.get("mode_counts", {}),
            "missing_modes": report.get("missing_modes", []),
            "error": report.get("error"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"REPORT_PATH={report_path}")
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a test-only V6 World Mind smoke with the legacy Qin Weixi Ollama model."
    )
    parser.add_argument("--model", default="qinweixi-qwen35")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--background-timeout-seconds", type=float, default=900.0)
    parser.add_argument(
        "--output-root",
        default=str(ROOT / "eval" / "world_mind_real_smoke"),
    )
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(_parser().parse_args())))
