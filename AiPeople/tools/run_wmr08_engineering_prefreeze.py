from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime._memory_vectors import BGE_DIMENSION, BGE_MODEL_ID, EncoderIdentity
from runtime._reranker import FakeMemoryReranker
from runtime.adapters import GenerationOptions, OllamaConfig, OllamaWorldMindBackend
from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ActiveSceneState,
    GameClockService,
    HeroineMemoryRepositoryFactory,
    InMemoryWorldStateProvider,
    LlamaCppHeroineMemoryProposer,
    LlamaCppWorldMindModel,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindModelIdentity,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
    CharacterPackagePromptComposer,
)
from runtime.world_mind.model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    GAME_REPLY,
    WORLD_CONTINUITY_REVIEW,
)
from runtime.world_mind.wmr08 import (
    build_engineering_freeze_manifest,
    environment_record,
    evaluate_engineering_prefreeze,
    nearest_rank_summary,
    write_json,
)

MODEL_SHA256 = "433f4f1cee5763af6007ae0ca882d45212ed5efcd0b196f3255af18d42e6c440"
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
        mode = _mode_from_request_id(request_id)
        payload: Mapping[str, object] = {}
        if response_format is not None:
            payload = json.loads(messages[-1]["content"])
            mode = str(payload.get("mode") or (
                "MIND_PATCH_V2"
                if payload.get("p") == "M2"
                else "FOREGROUND_SEMANTIC_TURN"
                if payload.get("p") == "M1"
                else "MEMORY_PROPOSE"
                if payload.get("p") == "R1"
                else mode
            ))
        record: dict[str, Any] = {
            "request_id": request_id,
            "mode": mode,
            "started_at": datetime.now().astimezone().isoformat(),
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
            if mode == WORLD_CONTINUITY_REVIEW:
                try:
                    record["critic_decision"] = json.loads(raw).get("decision")
                except (json.JSONDecodeError, AttributeError):
                    record["critic_decision"] = None
            return raw
        except BaseException as error:
            record["ok"] = False
            record["error_type"] = type(error).__name__
            record["error"] = str(error)
            raise
        finally:
            record["wall_total_ms"] = (time.perf_counter() - started) * 1000
            metrics = self.backend.last_generation_metrics
            if metrics is not None and metrics.request_id == request_id:
                record["generation"] = {
                    "first_content_ms": metrics.first_content_ms,
                    "total_ms": metrics.total_ms,
                    "prompt_eval_count": metrics.prompt_eval_count,
                    "eval_count": metrics.eval_count,
                    "load_duration_ms": metrics.load_duration_ms,
                    "prompt_eval_duration_ms": metrics.prompt_eval_duration_ms,
                    "eval_duration_ms": metrics.eval_duration_ms,
                }
            self.calls.append(record)


class TimedWorldStateProvider:
    def __init__(self, delegate: InMemoryWorldStateProvider) -> None:
        self.delegate = delegate
        self.get_latest_ms: list[float] = []

    async def update_latest(self, *args, **kwargs):
        return await self.delegate.update_latest(*args, **kwargs)

    async def get_latest(self, session):
        started = time.perf_counter()
        try:
            return await self.delegate.get_latest(session)
        finally:
            self.get_latest_ms.append((time.perf_counter() - started) * 1000)


class TimedMemoryRepository:
    def __init__(self, delegate, samples: list[float]) -> None:
        self.delegate = delegate
        self.samples = samples

    async def recall(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await self.delegate.recall(*args, **kwargs)
        finally:
            self.samples.append((time.perf_counter() - started) * 1000)

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


class TimedMemoryFactory:
    def __init__(self, delegate: HeroineMemoryRepositoryFactory) -> None:
        self.delegate = delegate
        self.recall_ms: list[float] = []
        self._repositories: dict[tuple[str, str, str], TimedMemoryRepository] = {}

    async def start(self) -> None:
        await self.delegate.start()

    async def close(self) -> None:
        await self.delegate.close()

    def open(self, session: RuntimeSessionIdentity) -> TimedMemoryRepository:
        key = (session.save_id, session.world_id, session.active_character_id)
        repository = self._repositories.get(key)
        if repository is None:
            repository = TimedMemoryRepository(self.delegate.open(session), self.recall_ms)
            self._repositories[key] = repository
        return repository


class EngineeringProbeEncoder:
    @property
    def identity(self) -> EncoderIdentity:
        return EncoderIdentity(
            model_id=BGE_MODEL_ID,
            revision="wmr08-engineering-test-double-v1",
            artifact_sha256=hashlib.sha256(b"wmr08-engineering-encoder").hexdigest(),
            dimension=BGE_DIMENSION,
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append(
                [float(digest[index % len(digest)] + 1) for index in range(BGE_DIMENSION)]
            )
        return vectors


class GpuSampler:
    def __init__(self, interval_seconds: float = 0.25) -> None:
        self.interval_seconds = interval_seconds
        self.samples: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.samples.append(await asyncio.to_thread(_sample_gpu))
        self._task = asyncio.create_task(self._run(), name="wmr08-gpu-sampler")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.samples.append(await asyncio.to_thread(_sample_gpu))

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            self.samples.append(await asyncio.to_thread(_sample_gpu))

    def summary(self) -> dict[str, Any]:
        values = [
            float(sample["system_used_mib"])
            for sample in self.samples
            if sample.get("system_used_mib") is not None
        ]
        return {
            "sample_interval_ms": self.interval_seconds * 1000,
            "sample_count": len(self.samples),
            "system_used_mib": nearest_rank_summary(values),
            "compute_processes_at_peak": max(
                (sample.get("compute_processes", []) for sample in self.samples),
                key=lambda rows: sum(float(row["used_mib"]) for row in rows),
                default=[],
            ),
        }


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
        save_id=f"wmr08_qin_probe_{identifier}",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=f"wmr08_qin_probe_{identifier}",
    )


def _world_state() -> tuple[ProtagonistLiveState, ActiveSceneState]:
    return (
        ProtagonistLiveState(
            protagonist_id="protagonist",
            location_id="apartment_table",
            location_label="出租屋餐桌旁",
            activity="吃面",
            body_state={"fatigue": "轻微疲惫", "injury": "无"},
            held_item_ids=("chopsticks",),
        ),
        ActiveSceneState(
            scene_id="apartment_table",
            location_label="出租屋餐桌旁",
            present_character_ids=("protagonist", "baiweixi"),
            item_states={
                "noodle_bowl": "放在男主面前，碗里还有面",
                "rain_window": "窗外仍在下雨",
            },
        ),
    )


async def _ollama_inventory(base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30) as client:
        version = (await client.get("/api/version")).json()
        tags = (await client.get("/api/tags")).json()
        processes = (await client.get("/api/ps")).json()
    return {"version": version, "tags": tags, "processes": processes}


async def _run(args: argparse.Namespace) -> int:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / f"qin-engineering-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "report.json"
    database_path = run_dir / "world_mind.sqlite3"
    source_sha256 = await asyncio.to_thread(_file_sha256, Path(args.source_gguf))
    if source_sha256 != MODEL_SHA256:
        raise RuntimeError("Qin Weixi probe GGUF SHA256 mismatch")

    model_record = {
        "ollama_name": args.model,
        "weights_character": "qinweixi_legacy",
        "artifact_sha256": source_sha256,
        "source_gguf": str(Path(args.source_gguf).resolve()),
        "context_size": args.context_size,
        "stream_response": True,
    }
    freeze_manifest = build_engineering_freeze_manifest(
        ROOT,
        probe_model=model_record,
    )
    write_json(run_dir / "freeze_manifest.json", freeze_manifest)

    session = _session(run_id.lower())
    store = WorldMindStore.open(database_path)
    clock = GameClockService(store, initial_game_time=INITIAL_GAME_TIME)
    provider = TimedWorldStateProvider(InMemoryWorldStateProvider())
    base_memory_factory = HeroineMemoryRepositoryFactory(
        store,
        encoder=EngineeringProbeEncoder(),
        reranker=FakeMemoryReranker(threshold=0.0),
    )
    memory_factory = TimedMemoryFactory(base_memory_factory)
    ollama = OllamaWorldMindBackend(
        OllamaConfig(
            model_name=args.model,
            base_url=args.base_url,
            context_size=args.context_size,
            keep_alive=args.keep_alive,
            request_timeout_seconds=args.timeout_seconds,
            stream_response=True,
        )
    )
    recording = RecordingBackend(ollama)
    model = LlamaCppWorldMindModel(
        recording,
        identity=WorldMindModelIdentity(
            model_id=f"ollama/{args.model}-legacy-engineering-prefreeze",
            revision="qin-legacy-wmr08-engineering-v1",
            artifact_sha256=source_sha256,
            character_id="baiweixi",
            world_id="songjiangfu",
            protagonist_id="protagonist",
        ),
    )
    memory_proposer = LlamaCppHeroineMemoryProposer(
        recording,
        identity=model.identity,
        character_prompt=CharacterPackagePromptComposer(_runtime_config())
        .compose(session)
        .system_prompt,
    )
    runtime = WorldMindRuntime(
        config=_runtime_config(),
        store=store,
        world_state_provider=provider,
        game_clock=clock,
        model=model,
        memory_repository_factory=memory_factory,
        memory_proposer_provider=lambda _session: memory_proposer,
        periodic_reconcile_seconds=300.0,
    )
    gpu_sampler = GpuSampler(args.gpu_sample_interval_seconds)
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "wmr08_engineering_prefreeze",
        "test_only": True,
        "not_accepted_as": [
            "白未晞角色质量验收",
            "白未晞正式模型验收",
            "正式 Embedding 与 Reranker 验收",
            "正式 P0 性能与稳定性结论",
        ],
        "started_at": datetime.now().astimezone().isoformat(),
        "environment": {
            **environment_record(),
            "processor": platform.processor(),
        },
        "model": model_record,
        "freeze_manifest": freeze_manifest,
        "memory_assets": {
            "embedding": "deterministic_engineering_test_double",
            "reranker": "deterministic_engineering_test_double",
            "formal_assets_deferred": True,
        },
        "turns": [],
        "calls": recording.calls,
    }
    exit_code = 1
    periodic_wall_ms: float | None = None
    try:
        await gpu_sampler.start()
        inventory_before = await _ollama_inventory(args.base_url)
        await runtime.start()
        protagonist, scene = _world_state()
        await provider.update_latest(
            session,
            protagonist,
            scene,
            clock.current_time(session),
        )
        prompts = (
            "我还在吃面。你现在在做什么？",
            "窗外雨还没停，你愿意陪我坐一会儿吗？",
            "我吃完了，但暂时还坐在桌边。",
        )
        for index in range(args.turn_samples):
            result = await runtime.handle_turn(
                TurnRequest(
                    request_id=f"wmr08-qin-turn-{run_id.lower()}-{index}",
                    session=session,
                    text=prompts[index % len(prompts)],
                )
            )
            report["turns"].append(
                {
                    "index": index,
                    "result_type": type(result).__name__,
                    "text": result.text if isinstance(result, Completed) else None,
                    "failure_code": result.code if isinstance(result, Failed) else None,
                    "metrics": (
                        {
                            "model_total_ms": result.metrics.model_total_ms,
                            "commit_ms": result.metrics.ledger_reply_commit_ms,
                            "total_ms": result.metrics.total_ms,
                        }
                        if isinstance(result, Completed)
                        else None
                    ),
                }
            )
            if not isinstance(result, Completed):
                raise RuntimeError(f"turn {index} failed: {result.code}")
            await runtime.wait_background_idle(
                session,
                timeout_seconds=args.background_timeout_seconds,
            )

        periodic_started = time.perf_counter()
        await runtime.trigger_periodic_reconcile(session)
        await runtime.wait_background_idle(
            session,
            timeout_seconds=args.background_timeout_seconds,
        )
        periodic_wall_ms = (time.perf_counter() - periodic_started) * 1000
        inventory_after = await _ollama_inventory(args.base_url)

        successful_modes = sorted(
            {call["mode"] for call in recording.calls if call.get("ok") is True}
        )
        report["successful_modes"] = successful_modes
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
            "memory_proposals": store.count_r1_memory_jobs(
                session.save_id, state="completed"
            ),
            "r1_memory_jobs_failed": store.count_r1_memory_jobs(
                session.save_id, state="failed"
            ),
        }
        report["performance"] = _performance_report(
            recording.calls,
            snapshot_ms=provider.get_latest_ms,
            recall_ms=memory_factory.recall_ms,
            periodic_wall_ms=periodic_wall_ms,
        )
        report["ollama"] = {
            "before": inventory_before,
            "after": inventory_after,
            "model_switching": "deferred_until_formal_multi_asset_stack",
            "kv_cache_breakdown": "not_exposed_by_ollama_api",
        }
        report["decision"] = evaluate_engineering_prefreeze(report)
        exit_code = 0 if report["decision"]["engineering_chain_passed"] else 1
    except BaseException as error:
        report.setdefault("successful_modes", [])
        report.setdefault("database", {})
        report.setdefault("performance", {})
        report["error_type"] = type(error).__name__
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        report["decision"] = evaluate_engineering_prefreeze(report)
    finally:
        try:
            await runtime.close()
        finally:
            store.close()
            await gpu_sampler.close()
        report["gpu"] = gpu_sampler.summary()
        report["calls"] = recording.calls
        report["finished_at"] = datetime.now().astimezone().isoformat()
        write_json(report_path, report)
        print(
            json.dumps(
                {
                    "decision": report["decision"],
                    "performance": report.get("performance"),
                    "gpu": report.get("gpu"),
                    "report_path": str(report_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return exit_code


def _performance_report(
    calls: Sequence[Mapping[str, Any]],
    *,
    snapshot_ms: Sequence[float],
    recall_ms: Sequence[float],
    periodic_wall_ms: float | None,
) -> dict[str, Any]:
    by_mode: dict[str, list[float]] = defaultdict(list)
    first_content_by_mode: dict[str, list[float]] = defaultdict(list)
    for call in calls:
        if call.get("ok") is not True:
            continue
        mode = str(call["mode"])
        generation = call.get("generation")
        if isinstance(generation, Mapping):
            total_ms = generation.get("total_ms")
            first_content_ms = generation.get("first_content_ms")
            if isinstance(total_ms, (int, float)):
                by_mode[mode].append(float(total_ms))
            if isinstance(first_content_ms, (int, float)):
                first_content_by_mode[mode].append(float(first_content_ms))
    critic_calls = [
        call for call in calls if call.get("mode") == WORLD_CONTINUITY_REVIEW
    ]
    critic_targets = [
        call
        for call in calls
        if call.get("mode")
        in {
            "TURN_MIND_ADVANCE",
            "POST_REPLY_WORLD_MIND_RECONCILE",
            "FIVE_MINUTE_WORLD_MIND_RECONCILE",
        }
    ]
    critic_jobs = {_logical_request_id(call) for call in critic_calls}
    candidate_jobs = {_logical_request_id(call) for call in critic_targets}
    return {
        "latest_snapshot_ms": nearest_rank_summary(snapshot_ms),
        "heroine_repository_recall_ms": nearest_rank_summary(recall_ms),
        "mode_total_ms": {
            mode: nearest_rank_summary(values) for mode, values in sorted(by_mode.items())
        },
        "mode_first_content_ms": {
            mode: nearest_rank_summary(values)
            for mode, values in sorted(first_content_by_mode.items())
        },
        "game_reply_first_content_ms": nearest_rank_summary(
            first_content_by_mode.get(GAME_REPLY, ())
        ),
        "game_reply_total_ms": nearest_rank_summary(by_mode.get(GAME_REPLY, ())),
        "critic": {
            "latency_ms": nearest_rank_summary(by_mode.get(WORLD_CONTINUITY_REVIEW, ())),
            "trigger_count": len(critic_jobs),
            "eligible_candidate_count": len(candidate_jobs),
            "trigger_rate": (
                len(critic_jobs) / len(candidate_jobs) if candidate_jobs else None
            ),
            "decisions": dict(
                Counter(
                    str(call.get("critic_decision"))
                    for call in critic_calls
                    if call.get("critic_decision")
                )
            ),
        },
        "five_minute_reconcile": {
            "model_ms": nearest_rank_summary(
                by_mode.get(FIVE_MINUTE_WORLD_MIND_RECONCILE, ())
            ),
            "wall_ms": periodic_wall_ms,
        },
        "background_foreground_interference": {
            "status": "covered_by_wmr06_wmr07_regression_not_formally_measured_here",
            "formal_measurement_deferred": True,
        },
        "one_hour_real_model_stability": {
            "status": "deferred_until_baiweixi_release_stack",
            "formal_measurement_deferred": True,
        },
    }


def _logical_request_id(call: Mapping[str, Any]) -> str:
    request_id = str(call.get("request_id", ""))
    parts = request_id.rsplit(":", 2)
    return parts[0] if len(parts) == 3 else request_id


def _mode_from_request_id(request_id: str) -> str:
    parts = str(request_id).rsplit(":", 2)
    return parts[-2] if len(parts) == 3 else "unknown"


def _sample_gpu() -> dict[str, Any]:
    used = _run_command(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ]
    )
    process_rows = _run_command(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    processes = []
    for row in process_rows.splitlines() if process_rows else ():
        parts = [part.strip() for part in row.split(",", 2)]
        if len(parts) != 3:
            continue
        try:
            processes.append(
                {"pid": int(parts[0]), "process_name": parts[1], "used_mib": float(parts[2])}
            )
        except ValueError:
            continue
    try:
        total = sum(float(line.strip()) for line in used.splitlines()) if used else None
    except ValueError:
        total = None
    return {
        "sampled_at": datetime.now().astimezone().isoformat(),
        "system_used_mib": total,
        "compute_processes": processes,
    }


def _run_command(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the WMR-08 engineering pre-freeze with the legacy Qin Weixi Ollama model."
    )
    parser.add_argument("--model", default="qinweixi-qwen35")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--background-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--turn-samples", type=int, default=1)
    parser.add_argument("--gpu-sample-interval-seconds", type=float, default=0.25)
    parser.add_argument(
        "--source-gguf",
        default=str(
            ROOT
            / "训练结果"
            / "qinweixi-qwen35-windows-ollama"
            / "qinweixi-qwen35-q4_k_m.gguf"
        ),
    )
    parser.add_argument(
        "--output-root",
        default=str(ROOT / "eval" / "world_mind_p0" / "runs"),
    )
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    arguments = _parser().parse_args()
    if arguments.turn_samples < 1:
        raise SystemExit("--turn-samples must be positive")
    sys.exit(asyncio.run(_run(arguments)))
