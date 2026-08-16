from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from runtime.adapters import (
    GenerationOptions,
    LlamaCppConfig,
    LlamaCppReplyModel,
    OllamaConfig,
    OllamaWorldMindBackend,
)


InferencePath = Literal["llama_cpp", "ollama"]
TaskPriority = Literal[
    "foreground_reply",
    "required_reconcile",
    "periodic_reconcile",
    "memory_index",
    "maintenance",
]

PRIORITY_ORDER: dict[TaskPriority, int] = {
    "foreground_reply": 0,
    "required_reconcile": 1,
    "periodic_reconcile": 2,
    "memory_index": 3,
    "maintenance": 4,
}


class Sys12ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Sys12ReleaseConfig:
    manifest_path: Path
    release_path: InferencePath
    diagnostic_path: InferencePath
    model_path: Path
    model_sha256: str
    model_bytes: int
    context_size: int
    parallel: int
    batch_size: int
    ubatch_size: int
    gpu_layers: str | int
    flash_attention: bool
    kv_cache_k: str
    kv_cache_v: str
    chat_template: str
    stop_sequences: tuple[str, ...]
    structured_output: str
    combined_gpu_peak_mib_max: int
    warm_first_visible_token_p95_ms_max: int
    reply_80_tokens_p95_ms_max: int
    reply_160_tokens_p95_ms_max: int
    memory_selection_incremental_p95_ms_max: int
    foreground_protocol: str
    world_mind_mode_timeouts_seconds: Mapping[str, float]

    @classmethod
    def load(cls, manifest_path: str | Path) -> "Sys12ReleaseConfig":
        path = Path(manifest_path).resolve()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Sys12ConfigError("SYS-12 release manifest is missing or invalid") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise Sys12ConfigError("SYS-12 release manifest schema_version must equal 1")
        if value.get("network_access_at_runtime") != "forbidden":
            raise Sys12ConfigError("runtime network access must be forbidden")
        if value.get("cloud_fallback") != "forbidden" or value.get("model_fallback") != "forbidden":
            raise Sys12ConfigError("release inference fallbacks must be forbidden")
        release_path = _path_name(value.get("release_path"), "release_path")
        diagnostic_path = _path_name(value.get("diagnostic_path"), "diagnostic_path")
        if release_path == diagnostic_path:
            raise Sys12ConfigError("release and diagnostic paths must be distinct")
        model = _mapping(value.get("model"), "model")
        model_path = Path(_text(model.get("path"), "model.path"))
        if not model_path.is_absolute() or not model_path.is_file():
            raise Sys12ConfigError("model.path must be an existing absolute file")
        model_sha256 = _sha256(model.get("sha256"), "model.sha256")
        model_bytes = _positive_int(model.get("bytes"), "model.bytes")
        if model_path.stat().st_size != model_bytes or _file_sha256(model_path) != model_sha256:
            raise Sys12ConfigError("model identity does not match the frozen artifact")
        compatibility = _mapping(
            model.get("runtime_compatibility"), "model.runtime_compatibility"
        )
        stop_sequences_value = compatibility.get("stop_sequences")
        if (
            not isinstance(stop_sequences_value, list)
            or not stop_sequences_value
            or any(not isinstance(item, str) or not item for item in stop_sequences_value)
            or len(set(stop_sequences_value)) != len(stop_sequences_value)
        ):
            raise Sys12ConfigError(
                "model.runtime_compatibility.stop_sequences must be unique non-empty strings"
            )
        structured_output = _text(
            compatibility.get("structured_output"),
            "model.runtime_compatibility.structured_output",
        )
        if structured_output not in {"json_schema", "none"}:
            raise Sys12ConfigError(
                "model.runtime_compatibility.structured_output is unsupported"
            )
        runtime = _mapping(value.get("runtime"), "runtime")
        foreground_protocol = _text(
            value.get("foreground_protocol"), "foreground_protocol"
        )
        if foreground_protocol != "mind_patch_v2":
            raise Sys12ConfigError(
                "SYS-12 release manifest must use mind_patch_v2"
            )
        mode_timeouts = _mapping(
            value.get("world_mind_mode_timeouts_seconds"),
            "world_mind_mode_timeouts_seconds",
        )
        expected_modes = {
            "MIND_PATCH_V2",
            "GAME_REPLY",
            "WORLD_CONTINUITY_REVIEW",
            "POST_REPLY_WORLD_MIND_RECONCILE",
            "FIVE_MINUTE_WORLD_MIND_RECONCILE",
        }
        if set(mode_timeouts) != expected_modes:
            raise Sys12ConfigError("world mind mode timeouts must cover every release mode")
        thresholds = _mapping(value.get("thresholds"), "thresholds")
        return cls(
            manifest_path=path,
            release_path=release_path,
            diagnostic_path=diagnostic_path,
            model_path=model_path.resolve(),
            model_sha256=model_sha256,
            model_bytes=model_bytes,
            context_size=_positive_int(runtime.get("context_size"), "runtime.context_size"),
            parallel=_positive_int(runtime.get("parallel"), "runtime.parallel"),
            batch_size=_positive_int(runtime.get("batch_size"), "runtime.batch_size"),
            ubatch_size=_positive_int(runtime.get("ubatch_size"), "runtime.ubatch_size"),
            gpu_layers=runtime.get("gpu_layers", "all"),
            flash_attention=runtime.get("flash_attention") is True,
            kv_cache_k=_text(runtime.get("kv_cache_k"), "runtime.kv_cache_k"),
            kv_cache_v=_text(runtime.get("kv_cache_v"), "runtime.kv_cache_v"),
            chat_template=_text(
                compatibility.get("chat_template"),
                "model.runtime_compatibility.chat_template",
            ),
            stop_sequences=tuple(stop_sequences_value),
            structured_output=structured_output,
            combined_gpu_peak_mib_max=_positive_int(thresholds.get("combined_gpu_peak_mib_max"), "thresholds.combined_gpu_peak_mib_max"),
            warm_first_visible_token_p95_ms_max=_positive_int(thresholds.get("warm_first_visible_token_p95_ms_max"), "thresholds.warm_first_visible_token_p95_ms_max"),
            reply_80_tokens_p95_ms_max=_positive_int(thresholds.get("reply_80_tokens_p95_ms_max"), "thresholds.reply_80_tokens_p95_ms_max"),
            reply_160_tokens_p95_ms_max=_positive_int(thresholds.get("reply_160_tokens_p95_ms_max"), "thresholds.reply_160_tokens_p95_ms_max"),
            memory_selection_incremental_p95_ms_max=_positive_int(thresholds.get("memory_selection_incremental_p95_ms_max"), "thresholds.memory_selection_incremental_p95_ms_max"),
            foreground_protocol=foreground_protocol,
            world_mind_mode_timeouts_seconds={
                name: float(_positive_int(value, f"world_mind_mode_timeouts_seconds.{name}"))
                for name, value in mode_timeouts.items()
            },
        )


@dataclass(slots=True)
class InferencePriorityMetrics:
    foreground_preemptions: int = 0
    tasks_started: int = 0
    tasks_completed: int = 0
    tasks_cancelled: int = 0
    peak_waiters: int = 0


class InferencePriorityGate:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active_priority: TaskPriority | None = None
        self._active_task: asyncio.Task[Any] | None = None
        self._waiters: dict[TaskPriority, int] = {priority: 0 for priority in PRIORITY_ORDER}
        self._closed = False
        self.metrics = InferencePriorityMetrics()

    @asynccontextmanager
    async def acquire(self, priority: TaskPriority) -> AsyncIterator[None]:
        if priority not in PRIORITY_ORDER:
            raise ValueError("unsupported inference task priority")
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("inference priority gate requires an asyncio task")
        async with self._condition:
            if self._closed:
                raise RuntimeError("inference priority gate is closed")
            self._waiters[priority] += 1
            self.metrics.peak_waiters = max(self.metrics.peak_waiters, sum(self._waiters.values()))
            active = self._active_task
            if (
                priority == "foreground_reply"
                and active is not None
                and self._active_priority is not None
                and PRIORITY_ORDER[self._active_priority] > PRIORITY_ORDER[priority]
            ):
                self.metrics.foreground_preemptions += 1
                active.cancel()
            try:
                await self._condition.wait_for(lambda: self._can_start(priority))
            finally:
                self._waiters[priority] -= 1
            self._active_priority = priority
            self._active_task = current
            self.metrics.tasks_started += 1
        try:
            yield
            self.metrics.tasks_completed += 1
        except asyncio.CancelledError:
            self.metrics.tasks_cancelled += 1
            raise
        finally:
            async with self._condition:
                if self._active_task is current:
                    self._active_priority = None
                    self._active_task = None
                self._condition.notify_all()

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            active = self._active_task
            if active is not None:
                active.cancel()
            self._condition.notify_all()

    def snapshot(self) -> dict[str, int]:
        return {
            "foreground_preemptions": self.metrics.foreground_preemptions,
            "tasks_started": self.metrics.tasks_started,
            "tasks_completed": self.metrics.tasks_completed,
            "tasks_cancelled": self.metrics.tasks_cancelled,
            "peak_waiters": self.metrics.peak_waiters,
        }

    def _can_start(self, priority: TaskPriority) -> bool:
        if self._closed:
            raise RuntimeError("inference priority gate is closed")
        if self._active_task is not None:
            return False
        rank = PRIORITY_ORDER[priority]
        return not any(count > 0 and PRIORITY_ORDER[name] < rank for name, count in self._waiters.items())


class PrioritizedChatBackend:
    def __init__(self, backend: object, gate: InferencePriorityGate) -> None:
        complete = getattr(backend, "complete_chat", None)
        if not callable(complete):
            raise TypeError("backend must provide complete_chat")
        self.backend = backend
        self.gate = gate

    @property
    def last_generation_stats(self):
        return getattr(self.backend, "last_generation_stats", None)

    @property
    def process_id(self) -> int | None:
        return getattr(self.backend, "process_id", None)

    async def start(self) -> None:
        await self.backend.start()

    async def close(self) -> None:
        await self.backend.close()

    async def complete_chat(
        self,
        *,
        request_id: str,
        messages,
        options: GenerationOptions,
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        priority = _request_priority(request_id)
        async with self.gate.acquire(priority):
            return await self.backend.complete_chat(
                request_id=request_id,
                messages=messages,
                options=options,
                response_format=response_format,
            )


@dataclass(frozen=True, slots=True)
class ReleaseHostMetrics:
    inference_path: InferencePath
    startup_ms: float
    shutdown_ms: float | None
    lifecycle_state: str


class Sys12ReleaseHost:
    def __init__(
        self,
        config: Sys12ReleaseConfig,
        *,
        inference_path: InferencePath | None = None,
    ) -> None:
        self.config = config
        self.inference_path = config.release_path if inference_path is None else inference_path
        if self.inference_path not in {config.release_path, config.diagnostic_path}:
            raise Sys12ConfigError("inference path is not allowed by the release manifest")
        manifest = json.loads(config.manifest_path.read_text(encoding="utf-8"))
        self.gate = InferencePriorityGate()
        if self.inference_path == "llama_cpp":
            llama = _mapping(manifest.get("llama_cpp"), "llama_cpp")
            server = Path(_text(llama.get("server_path"), "llama_cpp.server_path")).resolve()
            if not server.is_file() or _file_sha256(server) != _sha256(llama.get("server_sha256"), "llama_cpp.server_sha256"):
                raise Sys12ConfigError("llama.cpp server identity mismatch")
            raw_backend = LlamaCppReplyModel(
                LlamaCppConfig(
                    server_executable=server,
                    model_path=config.model_path,
                    manifest_path=config.manifest_path,
                    host=_text(llama.get("host"), "llama_cpp.host"),
                    port=_positive_int(llama.get("port"), "llama_cpp.port"),
                    model_alias=_text(llama.get("model_alias"), "llama_cpp.model_alias"),
                    context_size=config.context_size,
                    parallel=config.parallel,
                    batch_size=config.batch_size,
                    ubatch_size=config.ubatch_size,
                    gpu_layers=config.gpu_layers,
                    flash_attention=config.flash_attention,
                    kv_cache_k=config.kv_cache_k,
                    kv_cache_v=config.kv_cache_v,
                    chat_template=config.chat_template,
                    stop_sequences=config.stop_sequences,
                    structured_output=config.structured_output,
                    collect_usage=True,
                )
            )
        else:
            ollama = _mapping(manifest.get("ollama"), "ollama")
            raw_backend = OllamaWorldMindBackend(
                OllamaConfig(
                    model_name=_text(ollama.get("model_name"), "ollama.model_name"),
                    base_url=_text(ollama.get("base_url"), "ollama.base_url"),
                    context_size=config.context_size,
                    keep_alive=_text(ollama.get("keep_alive"), "ollama.keep_alive"),
                    stream_response=True,
                )
            )
        self.raw_backend = raw_backend
        self.backend = PrioritizedChatBackend(raw_backend, self.gate)
        self._state = "stopped"
        self._startup_ms = 0.0
        self._shutdown_ms: float | None = None

    @property
    def state(self) -> str:
        return self._state

    async def start(self) -> None:
        if self._state == "ready":
            return
        started = time.perf_counter()
        self._state = "starting"
        try:
            await self.backend.start()
        except BaseException:
            self._state = "failed"
            raise
        self._startup_ms = (time.perf_counter() - started) * 1000
        self._state = "ready"

    async def close(self) -> None:
        if self._state == "stopped":
            return
        started = time.perf_counter()
        self._state = "closing"
        try:
            await self.backend.close()
            await self.gate.close()
        finally:
            self._shutdown_ms = (time.perf_counter() - started) * 1000
            self._state = "stopped"

    def metrics(self) -> ReleaseHostMetrics:
        return ReleaseHostMetrics(
            inference_path=self.inference_path,
            startup_ms=self._startup_ms,
            shutdown_ms=self._shutdown_ms,
            lifecycle_state=self._state,
        )


def evaluate_sys12(report: Mapping[str, Any], config: Sys12ReleaseConfig) -> dict[str, Any]:
    release = _mapping(report.get("release_path"), "release_path")
    stability = _mapping(report.get("stability"), "stability")
    checks = {
        "release_path_frozen": release.get("name") == config.release_path,
        "asset_identity_verified": release.get("asset_sha256") == config.model_sha256,
        "repeatable_lifecycle": release.get("lifecycle_passed") is True,
        "gpu_peak": float(release.get("combined_gpu_peak_mib", float("inf"))) < config.combined_gpu_peak_mib_max,
        "warm_first_visible_p95": float(release.get("warm_first_visible_token_p95_ms", float("inf"))) < config.warm_first_visible_token_p95_ms_max,
        "reply_80_p95": float(release.get("reply_80_tokens_p95_ms", float("inf"))) < config.reply_80_tokens_p95_ms_max,
        "reply_160_p95": float(release.get("reply_160_tokens_p95_ms", float("inf"))) < config.reply_160_tokens_p95_ms_max,
        "memory_selection_p95": float(report.get("memory_selection_incremental_p95_ms", float("inf"))) < config.memory_selection_incremental_p95_ms_max,
        "foreground_priority": report.get("foreground_priority_passed") is True,
        "one_hour_release_stability": stability.get("minutes", 0) >= 60 and stability.get("passed") is True,
        "no_transaction_pollution": stability.get("transaction_violations") == 0,
    }
    passed = all(checks.values())
    return {
        "checks": checks,
        "sys12_gate": "passed" if passed else "failed",
        "release_performance_passed": passed,
    }


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Sys12ConfigError(f"{name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Sys12ConfigError(f"{name} must be non-empty text")
    return value.strip()


def _path_name(value: object, name: str) -> InferencePath:
    text = _text(value, name)
    if text not in {"llama_cpp", "ollama"}:
        raise Sys12ConfigError(f"{name} must be llama_cpp or ollama")
    return text  # type: ignore[return-value]


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise Sys12ConfigError(f"{name} must be a positive integer")
    return value


def _sha256(value: object, name: str) -> str:
    text = _text(value, name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise Sys12ConfigError(f"{name} must be lowercase SHA256")
    return text


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _request_priority(request_id: str) -> TaskPriority:
    if ":MEMORY_PROPOSE:" in request_id:
        return "memory_index"
    if ":FIVE_MINUTE_WORLD_MIND_RECONCILE:" in request_id:
        return "periodic_reconcile"
    if ":POST_REPLY_WORLD_MIND_RECONCILE:" in request_id:
        return "required_reconcile"
    return "foreground_reply"
