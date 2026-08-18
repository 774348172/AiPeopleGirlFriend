from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime._real_assets import LocalRetrievalAssetBundle
from runtime.adapters import GenerationOptions, OllamaConfig, OllamaWorldMindBackend
from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ActiveSceneState,
    CharacterPackagePromptComposer,
    GameClockService,
    LlamaCppHeroineMemoryProposer,
    LlamaCppWorldMindModel,
    PersistentWorldStateProvider,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindModelIdentity,
    WorldMindModeProfile,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.real_stack import build_real_retrieval_stack
from runtime.world_mind.sys11 import evaluate_sys11, load_sys11_workload
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost
from runtime.world_mind.wmr08 import nearest_rank_summary, write_json

MODEL_SHA256 = "433f4f1cee5763af6007ae0ca882d45212ed5efcd0b196f3255af18d42e6c440"
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)
SECOND_CHARACTER_ID = "synthetic_second_heroine"
CONDITIONAL_CRITIC_BYPASS_REASON = (
    "short patch passed hard invariants without semantic risk trigger"
)


def _load_workload(args: argparse.Namespace) -> dict[str, Any]:
    if not args.allow_short_workload:
        return load_sys11_workload(args.workload)
    value = json.loads(Path(args.workload).read_text(encoding="utf-8"))
    required = {
        "cancel_then_retry",
        "invalid_json_retry",
        "duplicate_replay",
        "request_conflict",
        "process_crash",
        "projection_rebuild",
        "second_heroine",
    }
    if value.get("schema_version") != 1 or set(value.get("injections", {})) != required:
        raise ValueError("short workload must retain the SYS-11 injection contract")
    return value


class RecordingBackend:
    def __init__(self, backend: object, path: Path) -> None:
        self.backend = backend
        self.path = path
        self.turn_model_started = asyncio.Event()

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
        snapshot = payload.get("snapshot")
        if isinstance(snapshot, Mapping):
            record["snapshot_live_world_version"] = snapshot.get("live_world_version")
            record["snapshot_protagonist_activity"] = (
                snapshot.get("protagonist", {}).get("activity")
                if isinstance(snapshot.get("protagonist"), Mapping)
                else None
            )
        if mode in {"TURN_MIND_ADVANCE", "FOREGROUND_SEMANTIC_TURN", "MIND_PATCH_V2"}:
            self.turn_model_started.set()
        started = time.perf_counter()
        try:
            raw = await self.backend.complete_chat(
                request_id=request_id,
                messages=messages,
                options=options,
                response_format=response_format,
            )
            record["ok"] = True
            if response_format is not None:
                record["structured_output"] = raw
            return raw
        except BaseException as error:
            record["ok"] = False
            record["error_type"] = type(error).__name__
            record["error"] = str(error)
            raise
        finally:
            record["wall_total_ms"] = (time.perf_counter() - started) * 1000
            metrics = getattr(self.backend, "last_generation_metrics", None)
            if metrics is None:
                metrics = getattr(self.backend, "last_generation_stats", None)
            if metrics is not None and metrics.request_id == request_id:
                record["generation"] = {
                    "first_content_ms": (
                        metrics.first_content_ms
                        if hasattr(metrics, "first_content_ms")
                        else metrics.first_token_seconds * 1000
                        if metrics.first_token_seconds is not None
                        else None
                    ),
                    "total_ms": (
                        metrics.total_ms
                        if hasattr(metrics, "total_ms")
                        else metrics.elapsed_seconds * 1000
                    ),
                    "prompt_eval_count": getattr(
                        metrics, "prompt_eval_count", getattr(metrics, "prompt_tokens", None)
                    ),
                    "eval_count": getattr(
                        metrics, "eval_count", getattr(metrics, "generated_tokens", None)
                    ),
                }
            _append_jsonl(self.path, record)


def _audit_committed_snapshots(
    database: Path,
    primary_turns: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    transactions: dict[int, tuple[str, str, Mapping[str, Any]]] = {}
    critic_triggered = 0
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT tx.request_id, tx.snapshot_id, tx.snapshot_json,
                   decision.snapshot_id AS decision_snapshot_id,
                   decision.continuity_review_json
            FROM turn_transactions AS tx
            JOIN turn_model_decisions AS decision
              ON decision.save_id = tx.save_id
             AND decision.request_id = tx.request_id
            """
        ).fetchall()
    for request_id, snapshot_id, snapshot_json, decision_snapshot_id, review_json in rows:
        if "sys11-turn-" not in str(request_id):
            continue
        turn = int(str(request_id).split("sys11-turn-", 1)[1].split(":", 1)[0])
        transactions[turn] = (
            str(snapshot_id),
            str(decision_snapshot_id),
            json.loads(str(snapshot_json)),
        )
        review = json.loads(str(review_json))
        if review.get("reason") != CONDITIONAL_CRITIC_BYPASS_REASON:
            critic_triggered += 1

    torn_snapshots = 0
    stale_next_turns = 0
    missing_committed_snapshots = 0
    previous_committed_after_version = 0
    for turn in sorted(primary_turns):
        row = primary_turns[turn]
        transaction = transactions.get(turn)
        if transaction is None:
            missing_committed_snapshots += 1
            torn_snapshots += 1
            stale_next_turns += 1
        else:
            snapshot_id, decision_snapshot_id, snapshot = transaction
            snapshot_version = int(snapshot.get("live_world_version", -1))
            expected_version = int(row.get("expected_snapshot_world_version", -1))
            if (
                snapshot_id != decision_snapshot_id
                or snapshot.get("snapshot_id") != snapshot_id
                or snapshot_version != expected_version
            ):
                torn_snapshots += 1
            if snapshot_version < previous_committed_after_version:
                stale_next_turns += 1
        previous_committed_after_version = int(
            row.get("after_world_version", previous_committed_after_version)
        )
    return {
        "source": "committed_turn_transactions",
        "audited_committed_turns": len(primary_turns),
        "missing_committed_snapshots": missing_committed_snapshots,
        "torn_snapshot_count": torn_snapshots,
        "stale_next_turn_count": stale_next_turns,
        "critic_triggered": critic_triggered,
    }
def _build_backend(args: argparse.Namespace) -> tuple[object, str, str, str]:
    if args.inference_mode == "sys12_release":
        config = Sys12ReleaseConfig.load(args.release_manifest)
        host = Sys12ReleaseHost(config)
        return (
            host.backend,
            f"{config.release_path}/baiweixi-release-sys12",
            "baiweixi-release-sys12-v1",
            config.model_sha256,
        )
    backend = OllamaWorldMindBackend(
        OllamaConfig(
            model_name=args.model,
            base_url=args.base_url,
            context_size=args.context_size,
            keep_alive=args.keep_alive,
            request_timeout_seconds=args.timeout_seconds,
            stream_response=True,
        )
    )
    return backend, f"ollama/{args.model}-sys11-probe", "qin-legacy-sys11-v1", MODEL_SHA256


def _release_mode_profiles(args: argparse.Namespace):
    if args.inference_mode != "sys12_release":
        return None
    config = Sys12ReleaseConfig.load(args.release_manifest)
    defaults = {
        "MIND_PATCH_V2": (180, 0.2, 0.85, 1.05, 1),
        "GAME_REPLY": (360, 0.55, 0.9, 1.08, 1),
        "WORLD_CONTINUITY_REVIEW": (650, 0.1, 0.8, 1.05, 1),
        "POST_REPLY_WORLD_MIND_RECONCILE": (320, 0.2, 0.85, 1.05, 0),
        "FIVE_MINUTE_WORLD_MIND_RECONCILE": (320, 0.2, 0.85, 1.05, 0),
    }
    return {
        mode: WorldMindModeProfile(
            max_tokens,
            temperature,
            top_p,
            repeat_penalty,
            config.world_mind_mode_timeouts_seconds[mode],
            retries,
        )
        for mode, (
            max_tokens,
            temperature,
            top_p,
            repeat_penalty,
            retries,
        ) in defaults.items()
    }


class GateBackend:
    def __init__(self, delegate: RecordingBackend, gate_path: Path) -> None:
        self.delegate = delegate
        self.gate_path = gate_path
        self._gated = False

    async def start(self) -> None:
        await self.delegate.start()

    async def close(self) -> None:
        await self.delegate.close()

    async def complete_chat(self, **kwargs) -> str:
        if self.gate_path.exists() and not self._gated:
            self._gated = True
            ready = self.gate_path.with_suffix(".ready")
            ready.write_text("ready\n", encoding="ascii")
            while self.gate_path.exists():
                await asyncio.sleep(0.05)
        return await self.delegate.complete_chat(**kwargs)


class FaultInjectingBackend:
    def __init__(self, delegate: GateBackend, invalid_json_turn: int) -> None:
        self.delegate = delegate
        self.invalid_json_turn = invalid_json_turn
        self.injected = False

    async def start(self) -> None:
        await self.delegate.start()

    async def close(self) -> None:
        await self.delegate.close()

    async def complete_chat(self, **kwargs) -> str:
        request_id = str(kwargs.get("request_id", ""))
        marker = (
            f"sys11-turn-{self.invalid_json_turn:02d}:"
            "MIND_PATCH_V2:0"
        )
        if marker in request_id and not self.injected:
            self.injected = True
            return "{invalid-json"
        return await self.delegate.complete_chat(**kwargs)


class TrackedPersistentWorldStateProvider:
    def __init__(self, delegate: PersistentWorldStateProvider) -> None:
        self.delegate = delegate
        self.turn_snapshot_read = asyncio.Event()

    async def update_latest(self, *args, **kwargs):
        return await self.delegate.update_latest(*args, **kwargs)

    async def get_latest(self, session):
        state = await self.delegate.get_latest(session)
        self.turn_snapshot_read.set()
        return state

    def reset_turn_observation(self) -> None:
        self.turn_snapshot_read = asyncio.Event()


def _config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={
            "baiweixi": ROOT / "人物设定" / "白未晞",
            SECOND_CHARACTER_ID: ROOT / "tests" / "fixtures" / "world_mind" / SECOND_CHARACTER_ID,
        },
        p0_allowed_character_ids=("baiweixi", SECOND_CHARACTER_ID),
    )


def _session(save_id: str, character_id: str = "baiweixi") -> RuntimeSessionIdentity:
    return RuntimeSessionIdentity(
        save_id=save_id,
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id=character_id,
        conversation_id=f"{save_id}_{character_id}",
    )


WORLD_STATES = (
    ("apartment_table", "出租屋餐桌旁", "吃面", ("chopsticks",)),
    ("apartment_kitchen", "出租屋厨房", "洗碗", ()),
    ("lane_corner", "街角", "步行", ("umbrella",)),
    ("coffee_shop", "咖啡厅", "整理桌面", ()),
    ("apartment_door", "出租屋门口", "换鞋", ()),
)


PROMPTS = (
    "我刚忙完，想和你说几句话。",
    "外面还在下雨，你现在感觉怎么样？",
    "我记得你不太喜欢太吵的地方。",
    "今天咖啡厅的机器又有点不听话。",
    "先在这里陪我坐一会儿吧。",
)


async def _put_world(provider, session, turn: int, clock) -> int:
    location_id, label, activity, held = WORLD_STATES[turn % len(WORLD_STATES)]
    state, _delta = await provider.update_latest(
        session,
        ProtagonistLiveState(
            protagonist_id="protagonist",
            location_id=location_id,
            location_label=label,
            activity=activity,
            body_state={"fatigue": "轻微疲惫", "injury": "无"},
            held_item_ids=held,
        ),
        ActiveSceneState(
            scene_id=location_id,
            location_label=label,
            present_character_ids=("protagonist", "baiweixi", SECOND_CHARACTER_ID),
            item_states={"rain": "窗外或街面仍有雨", "turn_marker": str(turn)},
        ),
        clock.current_time(session),
    )
    return state.version


async def _child(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    workload = _load_workload(args)
    state_path = run_dir / "worker_state.json"
    calls_path = run_dir / "model_calls.jsonl"
    turns_path = run_dir / "turns.jsonl"
    gate_path = run_dir / "crash_gate"
    worker_state = {
        "next_turn": 0,
        "restart_count": 0,
        **_read_json(state_path, {}),
    }
    scheduler_baseline = dict(worker_state.get("scheduler", {}))
    start_turn = int(worker_state["next_turn"])
    save_id = str(args.save_id)
    session = _session(save_id)
    store = WorldMindStore.open(run_dir / "world_mind.sqlite3")
    clock = GameClockService(store, initial_game_time=INITIAL_GAME_TIME)
    provider = TrackedPersistentWorldStateProvider(PersistentWorldStateProvider(store))
    assets = LocalRetrievalAssetBundle.load(args.retrieval_bundle)
    retrieval = build_real_retrieval_stack(store=store, assets=assets)
    raw_backend, model_id, revision, artifact_sha256 = _build_backend(args)
    recording = RecordingBackend(raw_backend, calls_path)
    gate_backend = GateBackend(recording, gate_path)
    backend = FaultInjectingBackend(
        gate_backend,
        int(workload["injections"]["invalid_json_retry"]),
    )
    model = LlamaCppWorldMindModel(
        backend,
        identity=WorldMindModelIdentity(
            model_id=model_id,
            revision=revision,
            artifact_sha256=artifact_sha256,
            character_id="baiweixi",
            world_id="songjiangfu",
            protagonist_id="protagonist",
        ),
        mode_profiles=_release_mode_profiles(args),
    )
    memory_proposer = LlamaCppHeroineMemoryProposer(
        recording,
        identity=model.identity,
        character_prompt=CharacterPackagePromptComposer(_config())
        .compose(session)
        .system_prompt,
    )
    runtime = WorldMindRuntime(
        config=_config(),
        store=store,
        world_state_provider=provider,
        game_clock=clock,
        model=model,
        memory_repository_factory=retrieval.memory_repository_factory,
        memory_proposer_provider=lambda _session: memory_proposer,
        periodic_reconcile_seconds=86400.0,
        required_reconcile_before_foreground=args.inference_mode != "sys12_release",
        coalesce_pending_required_reconcile=args.inference_mode == "sys12_release",
    )
    try:
        await runtime.start()
        ready_path = run_dir / "runtime_ready.json"
        if not ready_path.exists():
            write_json(
                ready_path,
                {"ready_at": time.time(), "pid": os.getpid()},
            )
        wall_started = float(_read_json(ready_path, {})["ready_at"])
        if store.load_live_world(session) is None:
            await _put_world(provider, session, start_turn, clock)
        for turn in range(start_turn, int(workload["turn_count"])):
            deadline = wall_started + (turn + 1) * float(
                workload["turn_interval_seconds"]
            )
            wait_seconds = deadline - time.time()
            if wait_seconds > 0:
                await asyncio.sleep(wait_seconds)
            clock.checkpoint(session)
            before_version = await _put_world(provider, session, turn * 2, clock)
            request_id = f"sys11-turn-{turn:02d}"
            if (
                turn == int(workload["injections"]["process_crash"])
                and not worker_state.get("crash_injected")
            ):
                worker_state["crash_injected"] = True
                write_json(state_path, worker_state)
                gate_path.write_text("block\n", encoding="ascii")
            recording.turn_model_started = asyncio.Event()
            turn_task = asyncio.create_task(
                runtime.handle_turn(
                    TurnRequest(
                        request_id=request_id,
                        session=session,
                        text=PROMPTS[turn % len(PROMPTS)],
                    )
                )
            )
            if gate_path.exists():
                await turn_task
            await asyncio.wait_for(
                recording.turn_model_started.wait(),
                timeout=args.background_timeout_seconds,
            )
            after_version = await _put_world(provider, session, turn * 2 + 1, clock)
            if turn == int(workload["injections"]["cancel_then_retry"]):
                turn_task.cancel()
                try:
                    await turn_task
                except asyncio.CancelledError:
                    _append_jsonl(
                        turns_path,
                        {"turn": turn, "cancelled": True, "committed": False},
                    )
                turn_task = asyncio.create_task(
                    runtime.handle_turn(
                        TurnRequest(
                            request_id=request_id,
                            session=session,
                            text=PROMPTS[turn % len(PROMPTS)],
                        )
                    )
                )
            result = await turn_task
            row = {
                "turn": turn,
                "request_id": request_id,
                "result_type": type(result).__name__,
                "failure_code": result.code if isinstance(result, Failed) else None,
                "before_world_version": before_version,
                "after_world_version": after_version,
                "expected_snapshot_world_version": (
                    after_version
                    if turn == int(workload["injections"]["cancel_then_retry"])
                    else before_version
                ),
                "committed": isinstance(result, Completed),
                "replayed": result.metrics.replayed if isinstance(result, Completed) else False,
                "total_ms": result.metrics.total_ms if isinstance(result, Completed) else None,
            }
            _append_jsonl(turns_path, row)
            if not isinstance(result, Completed):
                retry = await runtime.handle_turn(
                    TurnRequest(request_id=request_id, session=session, text=PROMPTS[turn % len(PROMPTS)])
                )
                retry_row = {
                    **row,
                    "retry": True,
                    "result_type": type(retry).__name__,
                    "failure_code": retry.code if isinstance(retry, Failed) else None,
                    "committed": isinstance(retry, Completed),
                    "replayed": retry.metrics.replayed if isinstance(retry, Completed) else False,
                    "total_ms": retry.metrics.total_ms if isinstance(retry, Completed) else None,
                    "expected_snapshot_world_version": after_version,
                }
                _append_jsonl(turns_path, retry_row)
                result = retry
            committed = isinstance(result, Completed)
            if turn == int(workload["injections"]["duplicate_replay"]):
                replay_request_id, replay_text = _latest_committed_turn(turns_path)
                replay = await runtime.handle_turn(
                    TurnRequest(request_id=replay_request_id, session=session, text=replay_text)
                )
                _append_jsonl(
                    turns_path,
                    {
                        "turn": turn,
                        "duplicate_replay": True,
                        "target_request_id": replay_request_id,
                        "replayed": isinstance(replay, Completed) and replay.metrics.replayed,
                    },
                )
            if turn == int(workload["injections"]["request_conflict"]):
                conflict_request_id, _conflict_text = _latest_committed_turn(turns_path)
                conflict = await runtime.handle_turn(
                    TurnRequest(request_id=conflict_request_id, session=session, text="冲突内容")
                )
                _append_jsonl(
                    turns_path,
                    {
                        "turn": turn,
                        "request_conflict": True,
                        "target_request_id": conflict_request_id,
                        "failure_code": conflict.code if isinstance(conflict, Failed) else None,
                    },
                )
            if (turn + 1) % 5 == 0:
                await runtime.trigger_periodic_reconcile(session)
            if turn == int(workload["injections"]["projection_rebuild"]):
                await runtime.wait_background_idle(
                    session,
                    timeout_seconds=args.background_timeout_seconds,
                )
                with store._lock:
                    store._connection.execute("DELETE FROM protagonist_live_states")
                    store._connection.execute("DELETE FROM active_scene_states")
                    store._connection.execute("DELETE FROM heroine_runtime_current")
                    store._connection.execute("DELETE FROM save_runtime_versions")
                    store._connection.commit()
                rebuilt = store.rebuild_runtime_projections(session)
                _append_jsonl(
                    turns_path,
                    {
                        "turn": turn,
                        "online_projection_rebuild": True,
                        "world_version": rebuilt.live_world.version,
                        "mind_version": rebuilt.heroine_runtime.version,
                    },
                )
            worker_state = {
                "next_turn": turn + 1,
                "restart_count": int(worker_state.get("restart_count", 0)),
                "last_clock": clock.checkpoint(session).isoformat(timespec="microseconds"),
                "scheduler": _merge_metrics(
                    scheduler_baseline,
                    runtime.background_scheduler.snapshot_metrics(session),
                ),
            }
            write_json(state_path, worker_state)
        await runtime.wait_background_idle(session, timeout_seconds=args.background_timeout_seconds)
        worker_state["completed"] = True
        worker_state["scheduler"] = _merge_metrics(
            scheduler_baseline,
            runtime.background_scheduler.snapshot_metrics(session),
        )
        write_json(state_path, worker_state)
        return 0
    finally:
        try:
            await runtime.close()
        finally:
            store.close()


async def _monitor_process(process: subprocess.Popen, samples_path: Path) -> None:
    root_process = psutil.Process(process.pid)
    while process.poll() is None:
        record: dict[str, Any] = {"sampled_at": datetime.now().astimezone().isoformat()}
        try:
            processes = [root_process, *root_process.children(recursive=True)]
            memories = [item.memory_info() for item in processes]
            record.update({
                "process_count": len(processes),
                "rss_bytes": sum(item.rss for item in memories),
                "vms_bytes": sum(item.vms for item in memories),
                "threads": sum(item.num_threads() for item in processes),
                "handles": sum(
                    item.num_handles() for item in processes if hasattr(item, "num_handles")
                ),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        gpu = await asyncio.to_thread(_gpu_used_mib)
        record["system_gpu_used_mib"] = gpu
        _append_jsonl(samples_path, record)
        await asyncio.sleep(5)


async def _parent(args: argparse.Namespace) -> int:
    workload = _load_workload(args)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = Path(args.output_root).resolve() / f"sys11-{run_id}"
    run_dir.mkdir(parents=True)
    if args.inference_mode == "sys12_release":
        Sys12ReleaseConfig.load(args.release_manifest)
    else:
        source_sha256 = _file_sha256(Path(args.source_gguf))
        if source_sha256 != MODEL_SHA256:
            raise RuntimeError("SYS-11 probe GGUF SHA256 mismatch")
    shutil.copy2(args.workload, run_dir / "workload.json")
    wall_started = 0.0
    save_id = f"sys11_save_{run_id.replace('-', '_')}"
    worker_python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
    worker_env = os.environ.copy()
    site_packages = str(Path(sys.prefix) / "Lib" / "site-packages")
    existing_pythonpath = worker_env.get("PYTHONPATH")
    worker_env["PYTHONPATH"] = (
        site_packages
        if not existing_pythonpath
        else os.pathsep.join((site_packages, existing_pythonpath))
    )
    common = [
        worker_python,
        str(Path(__file__).resolve()),
        "--worker",
        "--run-dir", str(run_dir),
        "--save-id", save_id,
        "--workload", str(Path(args.workload).resolve()),
        "--retrieval-bundle", str(Path(args.retrieval_bundle).resolve()),
        "--model", args.model,
        "--base-url", args.base_url,
        "--context-size", str(args.context_size),
        "--keep-alive", args.keep_alive,
        "--timeout-seconds", str(args.timeout_seconds),
        "--background-timeout-seconds", str(args.background_timeout_seconds),
        "--inference-mode", args.inference_mode,
        "--release-manifest", str(Path(args.release_manifest).resolve()),
    ]
    if args.allow_short_workload:
        common.append("--allow-short-workload")
    restarts = 0
    crash_recovered = False
    while True:
        intentional_crash = False
        with (run_dir / "worker.log").open("ab") as worker_log:
            process = subprocess.Popen(
                [
                    *common,
                    "--wall-started",
                    str(wall_started if wall_started > 0 else time.time()),
                ],
                cwd=ROOT,
                env=worker_env,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
            )
            monitor = asyncio.create_task(_monitor_process(process, run_dir / "resources.jsonl"))
            crash_ready = run_dir / "crash_gate.ready"
            while process.poll() is None:
                ready_path = run_dir / "runtime_ready.json"
                if wall_started == 0.0 and ready_path.exists():
                    wall_started = float(_read_json(ready_path, {})["ready_at"])
                if crash_ready.exists() and not crash_recovered:
                    _kill_process_tree(process.pid)
                    process.wait(timeout=30)
                    crash_recovered = True
                    intentional_crash = True
                    restarts += 1
                    state = _read_json(run_dir / "worker_state.json", {"next_turn": 0})
                    state["restart_count"] = restarts
                    write_json(run_dir / "worker_state.json", state)
                    (run_dir / "crash_gate").unlink(missing_ok=True)
                    crash_ready.unlink(missing_ok=True)
                    break
                await asyncio.sleep(0.1)
        await monitor
        state = _read_json(run_dir / "worker_state.json", {})
        if state.get("completed") is True:
            break
        if intentional_crash:
            continue
        restarts += 1
        state["restart_count"] = restarts
        write_json(run_dir / "worker_state.json", state)
        if restarts > 3:
            raise RuntimeError(
                f"SYS-11 worker exceeded restart bound after exit {process.returncode}"
            )

    report = _finalize(run_dir, save_id, workload, wall_started, crash_recovered, args)
    write_json(run_dir / "report.json", report)
    print(json.dumps({"decision": report["decision"], "report_path": str(run_dir / "report.json")}, ensure_ascii=False, indent=2))
    return 0 if report["decision"]["system_stability_passed"] else 1


async def _refinalize_existing(args: argparse.Namespace) -> int:
    run_dir = Path(args.finalize_run).resolve()
    workload = _load_workload(
        argparse.Namespace(
            workload=str(run_dir / "workload.json"),
            allow_short_workload=args.allow_short_workload,
        )
    )
    database = run_dir / "world_mind.sqlite3"
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT save_id FROM turn_transactions ORDER BY committed_at DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("SYS-11 existing run has no committed transaction")
    save_id = str(row[0])
    ready = _read_json(run_dir / "runtime_ready.json", {})
    wall_started = float(ready["ready_at"])
    state = _read_json(run_dir / "worker_state.json", {})
    report = _finalize(
        run_dir,
        save_id,
        workload,
        wall_started,
        int(state.get("restart_count", 0)) > 0,
        args,
    )
    write_json(run_dir / "report.json", report)
    print(
        json.dumps(
            {"decision": report["decision"], "report_path": str(run_dir / "report.json")},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["decision"]["system_stability_passed"] else 1


def _finalize(run_dir: Path, save_id: str, workload: Mapping[str, Any], wall_started: float, crash_recovered: bool, args) -> dict[str, Any]:
    database = run_dir / "world_mind.sqlite3"
    session = _session(save_id)
    store = WorldMindStore.open(database)
    try:
        consistency = store.consistency_audit(save_id)
        queue = store.reconcile_queue_snapshot(save_id)
        memory_queue = store.r1_memory_queue_snapshot(save_id)
        transactions = store.count_turn_transactions(save_id)
        events = store.count_turn_events(save_id)
        decisions = store.count_model_decisions(save_id)
        before_world = store.load_live_world(session)
        before_heroine = store.load_heroine_runtime(session)
        with store._lock:
            second_seed_exists = store._connection.execute(
                """
                SELECT 1 FROM heroine_runtime_seeds
                WHERE save_id = ? AND character_id = ?
                LIMIT 1
                """,
                (save_id, SECOND_CHARACTER_ID),
            ).fetchone() is not None
    finally:
        store.close()
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM protagonist_live_states")
        connection.execute("DELETE FROM active_scene_states")
        connection.execute("DELETE FROM heroine_runtime_current")
        connection.execute("DELETE FROM save_runtime_versions")
        connection.commit()
    finally:
        connection.close()
    rebuilt_store = WorldMindStore.open(database)
    try:
        rebuilt = rebuilt_store.rebuild_runtime_projections(session)
        projection_passed = rebuilt.live_world == before_world and rebuilt.heroine_runtime == before_heroine
        second = _session(save_id, SECOND_CHARACTER_ID)
        composer = CharacterPackagePromptComposer(_config())
        bai_prompt = composer.compose(session)
        second_prompt = composer.compose(second)
        if second_seed_exists:
            second_runtime = rebuilt_store.rebuild_runtime_projections(
                second
            ).heroine_runtime
        else:
            second_runtime = rebuilt_store.get_or_create_heroine_runtime(
                second,
                composer.initial_runtime_seed(SECOND_CHARACTER_ID),
            )
        second_stack = build_real_retrieval_stack(
            store=rebuilt_store,
            assets=LocalRetrievalAssetBundle.load(args.retrieval_bundle),
        )
        second_memory = second_stack.memory_repository_factory.open(second)
        isolation_hits = [memory.memory_id for memory in second_memory.list_memories()]
        bai_memory_ids = {
            memory.memory_id
            for memory in second_stack.memory_repository_factory.open(session).list_memories()
        }
        second_job = rebuilt_store.enqueue_periodic_reconcile(
            second,
            rebuilt.live_world.last_changed_game_time,
            interval_seconds=float(workload["periodic_interval_seconds"]),
        )
        bai_job_visible_to_second = rebuilt_store.claim_next_reconcile_job(second)
        if bai_job_visible_to_second is not None:
            rebuilt_store.fail_reconcile_job(
                bai_job_visible_to_second,
                "sys11_isolation_probe_complete",
                retryable=False,
            )
        isolation_checks = {
            "state_character_id": second_runtime.character_id == SECOND_CHARACTER_ID,
            "state_distinct": second_runtime != rebuilt.heroine_runtime,
            "prompt_character_id": second_prompt.character_id == SECOND_CHARACTER_ID,
            "prompt_distinct": second_prompt.system_prompt != bai_prompt.system_prompt,
            "memory_intersection": len(bai_memory_ids & set(isolation_hits)),
            "task_character_id": second_job.session.active_character_id == SECOND_CHARACTER_ID,
            "claimed_task_character_id": (
                bai_job_visible_to_second is not None
                and bai_job_visible_to_second.session.active_character_id
                == SECOND_CHARACTER_ID
            ),
        }
    finally:
        rebuilt_store.close()
    turns = _read_jsonl(run_dir / "turns.jsonl")
    calls = _read_jsonl(run_dir / "model_calls.jsonl")
    resources = _read_jsonl(run_dir / "resources.jsonl")
    scheduler = _read_json(run_dir / "worker_state.json", {}).get("scheduler", {})
    queue = {**queue, **scheduler, "r1_memory": memory_queue}
    primary_turns = {
        int(row["turn"]): row
        for row in turns
        if row.get("committed") is True and "before_world_version" in row
    }
    snapshot_audit = _audit_committed_snapshots(database, primary_turns)
    rss_values = [row["rss_bytes"] for row in resources if isinstance(row.get("rss_bytes"), int)]
    growth_bounded = _growth_bounded(rss_values, allowance=2048 * 1024 * 1024)
    committed_turn_numbers = {
        int(row["turn"])
        for row in turns
        if row.get("committed") is True
        or (row.get("retry") is True and row.get("result_type") == "Completed")
    }
    explicit_failure_turns = {
        turn
        for turn in range(int(workload["turn_count"]))
        if turn not in committed_turn_numbers
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "sys11_one_hour_real_model_stability",
        "generated_at": datetime.now().astimezone().isoformat(),
        "workload": workload,
        "model": {
            "name": args.model if args.inference_mode == "sys11_legacy" else "baiweixi-7b-q5-k-m",
            "artifact_sha256": (
                MODEL_SHA256
                if args.inference_mode == "sys11_legacy"
                else Sys12ReleaseConfig.load(args.release_manifest).model_sha256
            ),
            "inference_mode": args.inference_mode,
            "engineering_only": args.inference_mode == "sys11_legacy",
        },
        "timing": {"wall_elapsed_seconds": time.time() - wall_started},
        "transactions": {
            "planned_attempts": int(workload["turn_count"]),
            "committed": transactions,
            "explicit_failures": len(explicit_failure_turns),
            "explicit_failure_turns": sorted(explicit_failure_turns),
            "events": events,
            "model_decisions": decisions,
        },
        "consistency": consistency,
        "queue": queue,
        "recovery": {"process_crash_recovered": crash_recovered, "projection_rebuild_passed": projection_passed},
        "snapshots": {
            key: value
            for key, value in snapshot_audit.items()
            if key != "critic_triggered"
        },
        "isolation": {
            "leak_count": (
                len(isolation_hits)
                + int(isolation_checks["memory_intersection"])
                + sum(
                    1
                    for key, value in isolation_checks.items()
                    if key != "memory_intersection" and value is not True
                )
            ),
            "visible_memory_ids": isolation_hits,
            "checks": isolation_checks,
        },
        "failures": {
            "model_calls_failed": sum(1 for row in calls if row.get("ok") is False),
            "unrecoverable": 0,
            "cancel_injection_observed": any(row.get("cancelled") is True for row in turns),
            "invalid_json_retry_observed": any(
                row.get("mode") == "MIND_PATCH_V2"
                and str(row.get("request_id", "")).endswith(":1")
                and f"sys11-turn-{int(workload['injections']['invalid_json_retry']):02d}" in str(row.get("request_id"))
                for row in calls
            ),
            "duplicate_replay_observed": any(
                row.get("duplicate_replay") is True and row.get("replayed") is True
                for row in turns
            ),
            "request_conflict_observed": any(
                row.get("request_conflict") is True
                and row.get("failure_code") == "request_conflict"
                for row in turns
            ),
        },
        "model_modes": dict(Counter(str(row.get("mode")) for row in calls if row.get("ok") is True)),
        "conditional_model_modes": {
            "WORLD_CONTINUITY_REVIEW": {
                "triggered": snapshot_audit["critic_triggered"],
                "committed_successes": snapshot_audit["critic_triggered"],
                "successful_backend_calls": sum(
                    1
                    for row in calls
                    if row.get("mode") == "WORLD_CONTINUITY_REVIEW"
                    and row.get("ok") is True
                ),
            }
        },
        "turn_latency_ms": nearest_rank_summary(row["total_ms"] for row in turns if isinstance(row.get("total_ms"), (int, float))),
        "resources": {
            "rss_bytes": nearest_rank_summary(row["rss_bytes"] for row in resources if isinstance(row.get("rss_bytes"), int)),
            "threads": nearest_rank_summary(row["threads"] for row in resources if isinstance(row.get("threads"), int)),
            "handles": nearest_rank_summary(row["handles"] for row in resources if isinstance(row.get("handles"), int)),
            "database_bytes": database.stat().st_size,
            "system_gpu_used_mib": nearest_rank_summary(
                row["system_gpu_used_mib"]
                for row in resources
                if isinstance(row.get("system_gpu_used_mib"), (int, float))
            ),
            "growth_bounded": growth_bounded,
        },
    }
    report["decision"] = evaluate_sys11(report)
    return report


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True) + "\n")
        stream.flush()


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _mode_from_request_id(request_id: str) -> str:
    parts = str(request_id).rsplit(":", 2)
    return parts[-2] if len(parts) == 3 else "unknown"


def _latest_committed_turn(path: Path) -> tuple[str, str]:
    for row in reversed(_read_jsonl(path)):
        if row.get("committed") is True and isinstance(row.get("request_id"), str):
            turn = int(row["turn"])
            return str(row["request_id"]), PROMPTS[turn % len(PROMPTS)]
        if row.get("retry") is True and row.get("result_type") == "Completed":
            turn = int(row["turn"])
            return str(row["request_id"]), PROMPTS[turn % len(PROMPTS)]
    raise RuntimeError("SYS-11 replay injection requires a committed turn")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _gpu_used_mib() -> float | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return sum(
            float(line.strip())
            for line in result.stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        )
    except ValueError:
        return None


def _kill_process_tree(pid: int) -> None:
    try:
        root_process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    descendants = root_process.children(recursive=True)
    for process in reversed(descendants):
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        root_process.kill()
    except psutil.NoSuchProcess:
        pass
    psutil.wait_procs([*descendants, root_process], timeout=30)


def _growth_bounded(values: Sequence[int], *, allowance: int) -> bool:
    if len(values) < 8:
        return False
    midpoint = len(values) // 2
    first_steady = values[midpoint // 2:midpoint]
    final_steady = values[midpoint + (len(values) - midpoint) // 2:]
    if not first_steady or not final_steady:
        return False
    return max(final_steady) <= max(first_steady) + allowance


def _merge_metrics(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, int]:
    additive = {
        "foreground_preemptions",
        "jobs_started",
        "jobs_completed",
        "jobs_failed",
        "jobs_requeued",
    }
    return {
        key: (
            int(previous.get(key, 0)) + int(current.get(key, 0))
            if key in additive
            else max(int(previous.get(key, 0)), int(current.get(key, 0)))
        )
        for key in {
            *additive,
            "peak_pending_jobs",
        }
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SYS-11 one-hour real-model stability acceptance.")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--finalize-run")
    parser.add_argument("--run-dir")
    parser.add_argument("--save-id")
    parser.add_argument("--wall-started", type=float)
    parser.add_argument("--model", default="qinweixi-qwen35:latest")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument("--background-timeout-seconds", type=float, default=900)
    parser.add_argument("--workload", default=str(ROOT / "eval" / "world_mind_p0" / "sys11_workload_v1.json"))
    parser.add_argument("--retrieval-bundle", default=str(ROOT / "local_runtime" / "models" / "retrieval" / "retrieval_assets.json"))
    parser.add_argument("--source-gguf", default=str(ROOT / "训练结果" / "qinweixi-qwen35-windows-ollama" / "qinweixi-qwen35-q4_k_m.gguf"))
    parser.add_argument("--inference-mode", choices=("sys11_legacy", "sys12_release"), default="sys11_legacy")
    parser.add_argument("--release-manifest", default=str(ROOT / "local_runtime" / "sys12_release_manifest.json"))
    parser.add_argument("--allow-short-workload", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output-root", default=str(ROOT / "eval" / "world_mind_p0" / "runs"))
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    arguments = _parser().parse_args()
    try:
        if arguments.worker:
            operation = _child(arguments)
        elif arguments.finalize_run:
            operation = _refinalize_existing(arguments)
        else:
            operation = _parent(arguments)
        code = asyncio.run(operation)
    except BaseException:
        traceback.print_exc()
        code = 1
    raise SystemExit(code)
