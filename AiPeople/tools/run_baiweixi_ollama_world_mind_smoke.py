from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
import time
import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from runtime.adapters import GenerationOptions, OllamaConfig, OllamaWorldMindBackend
from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ActiveSceneState,
    CharacterPackagePromptComposer,
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
MODEL_ROOT = Path(os.environ.get("AIPEOPLE_MODEL_ROOT", "F:/AiPeople"))
DEFAULT_ARTIFACT_PATH = (
    MODEL_ROOT
    / "training_package_baiweixi_local3060_qwen3"
    / "outputs"
    / "baiweixi_4b_q5_k_m.gguf"
)
EXPECTED_ARTIFACT_SHA256 = (
    "7786060133153d8ed0dd25f399027deee5d18f6d9b0f56f62bd13b9468a93a0f"
)
EXPECTED_OLLAMA_DIGEST = (
    "09d23dc30422a78343d65dd1c566f92287917c82856c6c1aa1126b2c43dd58fa"
)
EXPECTED_MODES = {
    TURN_MIND_ADVANCE,
    WORLD_CONTINUITY_REVIEW,
    GAME_REPLY,
    POST_REPLY_WORLD_MIND_RECONCILE,
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
}
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)
ROLE_CASES = (
    {
        "case_id": "identity_name",
        "prompt": "你叫什么名字？",
        "required_any": ("白未晞",),
        "forbidden": ("秦未晞", "秦未希"),
    },
    {
        "case_id": "identity_species",
        "prompt": "你到底是人还是妖？",
        "required_any": ("猫妖", "妖"),
        "forbidden": ("AI", "人工智能"),
    },
    {
        "case_id": "world_city",
        "prompt": "我们现在住的城市叫什么？",
        "required_any": ("松江府",),
        "forbidden": ("上海", "金陵"),
    },
    {
        "case_id": "origin",
        "prompt": "你小时候在哪里生活？",
        "required_any": ("深山", "山林", "林子", "森林"),
        "forbidden": (),
    },
    {
        "case_id": "reluctant_departure",
        "prompt": "等你的伤完全好了，你是不是马上就会离开这里？",
        "required_any": (
            "不会马上",
            "不急",
            "暂时",
            "再说",
            "没想好",
            "舍不得",
            "不想",
            "安全",
            "不会赶我走",
            "留一阵",
            "留下",
        ),
        "forbidden": (),
    },
    {
        "case_id": "natural_cat_voice",
        "prompt": "你现在最想吃什么？",
        "required_any": ("鱼", "甜", "吃"),
        "forbidden": (),
    },
)


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
        mode = "ROLE_SMOKE"
        try:
            payload = json.loads(messages[-1]["content"])
            if isinstance(payload, dict):
                mode = str(payload.get("mode", mode))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
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
        save_id=f"baiweixi_candidate_{identifier}",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=f"baiweixi_candidate_{identifier}",
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


async def _ollama_model_metadata(
    base_url: str,
    model_name: str,
    expected_digest: str,
) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
    models = response.json().get("models", [])
    aliases = {model_name, f"{model_name}:latest"}
    match = next(
        (
            item
            for item in models
            if isinstance(item, dict)
            and (item.get("name") in aliases or item.get("model") in aliases)
        ),
        None,
    )
    if match is None:
        raise RuntimeError(f"Ollama model is not installed: {model_name}")
    actual_digest = str(match.get("digest", "")).lower()
    if actual_digest != expected_digest:
        raise RuntimeError(
            "Ollama model digest mismatch: "
            f"expected {expected_digest}, got {actual_digest or 'missing'}"
        )
    details = match.get("details")
    return {
        "name": match.get("name"),
        "digest": actual_digest,
        "size": match.get("size"),
        "details": details if isinstance(details, dict) else {},
    }


async def _run_role_smoke(
    backend: RecordingBackend,
    session: RuntimeSessionIdentity,
) -> dict[str, object]:
    prompt = CharacterPackagePromptComposer(_runtime_config()).compose(session)
    results: list[dict[str, object]] = []
    for case in ROLE_CASES:
        response = await backend.complete_chat(
            request_id=f"role-smoke:{case['case_id']}",
            messages=(
                {"role": "system", "content": prompt.system_prompt},
                {"role": "user", "content": str(case["prompt"])},
            ),
            options=GenerationOptions(
                max_tokens=180,
                temperature=0.55,
                top_p=0.9,
                repeat_penalty=1.1,
                seed=42,
            ),
        )
        required_any = tuple(str(item) for item in case["required_any"])
        forbidden = tuple(str(item) for item in case["forbidden"])
        required_passed = any(item in response for item in required_any)
        forbidden_hits = tuple(item for item in forbidden if item in response)
        results.append(
            {
                "case_id": case["case_id"],
                "prompt": case["prompt"],
                "response": response,
                "required_any": required_any,
                "forbidden_hits": forbidden_hits,
                "passed": required_passed and not forbidden_hits,
            }
        )
    meow_responses = sum("喵" in str(item["response"]) for item in results)
    aggregate = {
        "responses_with_meow": meow_responses,
        "mechanical_meow_passed": meow_responses <= 1,
    }
    return {
        "cases": results,
        "aggregate": aggregate,
        "passed": all(bool(item["passed"]) for item in results)
        and bool(aggregate["mechanical_meow_passed"]),
    }


def _read_audited_identities(database_path: Path) -> dict[str, object]:
    with sqlite3.connect(database_path) as connection:
        turn_rows = connection.execute(
            "SELECT model_identity_json FROM turn_model_decisions ORDER BY committed_at"
        ).fetchall()
        reconcile_rows = connection.execute(
            """
            SELECT model_identity_json
            FROM world_mind_reconcile_decisions
            ORDER BY committed_at
            """
        ).fetchall()
    return {
        "turn": [json.loads(row[0]) for row in turn_rows],
        "reconcile": [json.loads(row[0]) for row in reconcile_rows],
    }


async def _run(args: argparse.Namespace) -> int:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / f"baiweixi-qwen3-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "report.json"
    database_path = run_dir / "world_mind.sqlite3"
    artifact_path = Path(args.artifact_path).resolve()
    artifact_sha256 = _sha256_file(artifact_path)
    if artifact_sha256 != args.artifact_sha256:
        raise RuntimeError(
            "GGUF SHA256 mismatch: "
            f"expected {args.artifact_sha256}, got {artifact_sha256}"
        )
    ollama_metadata = await _ollama_model_metadata(
        args.base_url,
        args.model,
        args.ollama_digest,
    )
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
    identity = WorldMindModelIdentity(
        model_id=f"ollama/{args.model}",
        revision=f"ollama-digest:{args.ollama_digest}",
        artifact_sha256=artifact_sha256,
        character_id="baiweixi",
        world_id="songjiangfu",
        protagonist_id="protagonist",
    )
    expected_identity = {
        "model_id": identity.model_id,
        "revision": identity.revision,
        "artifact_sha256": identity.artifact_sha256,
        "character_id": identity.character_id,
        "world_id": identity.world_id,
        "protagonist_id": identity.protagonist_id,
    }
    model = LlamaCppWorldMindModel(
        recording,
        identity=identity,
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
        "acceptance_scope": "白未晞正式候选工程链路与基础角色 Smoke",
        "not_accepted_as": [
            "完整角色质量验收",
            "正式性能 P95",
            "一小时稳定性验收",
            "P0 正式冻结",
        ],
        "model": {
            "ollama_name": args.model,
            "ollama_digest": args.ollama_digest,
            "ollama_metadata": ollama_metadata,
            "artifact_path": str(artifact_path),
            "artifact_sha256": artifact_sha256,
            "base_url": args.base_url,
            "context_size": args.context_size,
        },
        "runtime_binding": {
            "character_id": "baiweixi",
            "world_id": "songjiangfu",
            "protagonist_id": "protagonist",
            "save_id": session.save_id,
            "database_path": str(database_path),
        },
        "started_at": datetime.now().astimezone().isoformat(),
        "turn": {},
        "database": {},
        "role_smoke": {},
        "turn_semantic_smoke": {},
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
                request_id=f"baiweixi-candidate-turn-{run_id.lower()}",
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
        turn_semantic_failures = tuple(
            phrase
            for phrase in ("我还没吃完", "你现在在做什么")
            if phrase in result.text
        )
        report["turn_semantic_smoke"] = {
            "expected_program_fact": "男主正在吃面",
            "response": result.text,
            "failure_signals": turn_semantic_failures,
            "passed": not turn_semantic_failures,
        }

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
        audited = _read_audited_identities(database_path)
        audited_values = tuple(audited["turn"]) + tuple(audited["reconcile"])
        report["model_identity_audit"] = {
            "expected": expected_identity,
            "records": audited,
            "passed": bool(audited_values)
            and all(item == expected_identity for item in audited_values),
        }
        report["mode_counts"] = dict(sorted(mode_counts.items()))
        report["missing_modes"] = sorted(EXPECTED_MODES - successful_modes)
        report["role_smoke"] = await _run_role_smoke(recording, session)
        report["engineering_passed"] = (
            not report["missing_modes"]
            and report["database"]["model_decisions"] == 1
            and report["database"]["reconcile_jobs_completed"] >= 2
            and report["database"]["reconcile_jobs_failed"] == 0
            and report["database"]["reconcile_decisions"] >= 2
            and report["model_identity_audit"]["passed"]
        )
        report["quality_smoke_passed"] = (
            report["role_smoke"]["passed"]
            and report["turn_semantic_smoke"]["passed"]
        )
        report["passed"] = report["engineering_passed"]
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
            "engineering_passed": report.get("engineering_passed", False),
            "quality_smoke_passed": report.get("quality_smoke_passed", False),
            "turn": report.get("turn", {}),
            "turn_semantic_smoke": report.get("turn_semantic_smoke", {}),
            "database": report.get("database", {}),
            "model_identity_audit": report.get("model_identity_audit", {}),
            "mode_counts": report.get("mode_counts", {}),
            "missing_modes": report.get("missing_modes", []),
            "role_smoke": report.get("role_smoke", {}),
            "error": report.get("error"),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"REPORT_PATH={report_path}")
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run V6 World Mind and role smoke with the Bai Weixi Ollama model."
    )
    parser.add_argument("--model", default="baiweixi")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--background-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--artifact-path", default=str(DEFAULT_ARTIFACT_PATH))
    parser.add_argument("--artifact-sha256", default=EXPECTED_ARTIFACT_SHA256)
    parser.add_argument("--ollama-digest", default=EXPECTED_OLLAMA_DIGEST)
    parser.add_argument(
        "--output-root",
        default=str(ROOT / "eval" / "world_mind_real_smoke"),
    )
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(_parser().parse_args())))
