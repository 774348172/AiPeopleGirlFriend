from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import socket
import subprocess
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from runtime._context import ReplyContext, initial_reply_context
from runtime._model import ReplyRequest
from runtime._prompt import build_reply_messages


LOGGER = logging.getLogger(__name__)


class LlamaCppError(RuntimeError):
    pass


class LlamaCppAssetError(LlamaCppError):
    pass


class LlamaCppStartupError(LlamaCppError):
    pass


class LlamaCppNotReadyError(LlamaCppError):
    pass


class LlamaCppProtocolError(LlamaCppError):
    pass


class _RecoverableServerError(LlamaCppError):
    pass


@dataclass(frozen=True, slots=True)
class GenerationStats:
    request_id: str
    first_token_seconds: float | None
    prompt_tokens: int | None
    generated_tokens: int | None
    prompt_tokens_per_second: float | None
    generated_tokens_per_second: float | None
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    max_tokens: int
    temperature: float
    top_p: float
    repeat_penalty: float
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if not 0 <= self.temperature <= 2 or not 0 < self.top_p <= 1:
            raise ValueError("invalid sampling parameters")
        if self.repeat_penalty <= 0:
            raise ValueError("repeat_penalty must be positive")
        if self.seed is not None and not 0 <= self.seed <= 2_147_483_647:
            raise ValueError("seed is outside the supported range")


@dataclass(frozen=True, slots=True)
class LlamaCppConfig:
    server_executable: Path
    model_path: Path
    manifest_path: Path
    host: str = "127.0.0.1"
    port: int = 18081
    model_alias: str = "heroine_reply"
    context_size: int = 4096
    parallel: int = 1
    batch_size: int = 512
    ubatch_size: int = 128
    gpu_layers: str | int = "all"
    flash_attention: bool = True
    kv_cache_k: str = "q8_0"
    kv_cache_v: str = "q8_0"
    startup_timeout_seconds: float = 240.0
    connect_timeout_seconds: float = 5.0
    read_idle_timeout_seconds: float = 30.0
    generation_timeout_seconds: float = 90.0
    shutdown_timeout_seconds: float = 10.0
    max_tokens: int = 160
    temperature: float = 0.75
    top_p: float = 0.9
    repeat_penalty: float = 1.1
    seed: int | None = None
    collect_usage: bool = False
    chat_template: str | None = None
    stop_sequences: tuple[str, ...] = ()
    structured_output: str = "json_schema"
    server_prefix_args: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("server_executable", "model_path", "manifest_path"):
            path = Path(getattr(self, field_name))
            if not path.is_absolute():
                raise ValueError(f"{field_name} must be an absolute path")
            object.__setattr__(self, field_name, path)
        if self.host != "127.0.0.1":
            raise ValueError("host must be 127.0.0.1")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if not self.model_alias.strip():
            raise ValueError("model_alias cannot be empty")
        if self.chat_template is not None and not self.chat_template.strip():
            raise ValueError("chat_template cannot be blank")
        if any(not isinstance(item, str) or not item for item in self.stop_sequences):
            raise ValueError("stop_sequences must contain non-empty strings")
        if len(set(self.stop_sequences)) != len(self.stop_sequences):
            raise ValueError("stop_sequences cannot contain duplicates")
        if self.structured_output not in {"json_schema", "none"}:
            raise ValueError("structured_output must be json_schema or none")
        if (
            self.context_size <= 0
            or self.parallel != 1
            or self.batch_size <= 0
            or self.ubatch_size <= 0
            or self.ubatch_size > self.batch_size
            or self.max_tokens <= 0
        ):
            raise ValueError("context/batch/max_tokens must be positive, ubatch <= batch, and parallel must be 1")
        if not 0 <= self.temperature <= 2 or not 0 < self.top_p <= 1:
            raise ValueError("invalid sampling parameters")
        if self.repeat_penalty <= 0:
            raise ValueError("repeat_penalty must be positive")
        for name in (
            "startup_timeout_seconds",
            "connect_timeout_seconds",
            "read_idle_timeout_seconds",
            "generation_timeout_seconds",
            "shutdown_timeout_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_manifest(
        cls,
        manifest_path: Path,
        *,
        port: int | None = None,
        max_tokens: int = 160,
        seed: int | None = None,
        collect_usage: bool = False,
    ) -> LlamaCppConfig:
        path = Path(manifest_path).resolve()
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            llama_cpp = manifest["llama_cpp"]
            model = manifest["model"]
            launch = manifest["launch"]
            verification = manifest.get("verification", {})
            server_path = Path(llama_cpp["server_path"])
            model_path = Path(model["path"])
            model_alias = verification.get("model_alias", "heroine_reply")
            compatibility = model.get("runtime_compatibility", {})
            chat_template = compatibility.get("chat_template")
            stop_sequences = tuple(compatibility.get("stop_sequences", ()))
            structured_output = compatibility.get("structured_output", "json_schema")
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise LlamaCppAssetError("manifest runtime configuration is invalid") from error
        return cls(
            server_executable=server_path,
            model_path=model_path,
            manifest_path=path,
            host=launch.get("host", "127.0.0.1"),
            port=launch["port"] if port is None else port,
            model_alias=model_alias,
            context_size=launch.get("context_size", 4096),
            parallel=launch.get("parallel", 1),
            batch_size=launch.get("batch_size", 512),
            ubatch_size=launch.get("ubatch_size", 128),
            gpu_layers=launch.get("gpu_layers", "all"),
            flash_attention=launch.get("flash_attention", True),
            kv_cache_k=launch.get("kv_cache_k", "q8_0"),
            kv_cache_v=launch.get("kv_cache_v", "q8_0"),
            max_tokens=max_tokens,
            seed=seed,
            collect_usage=collect_usage,
            chat_template=chat_template,
            stop_sequences=stop_sequences,
            structured_output=structured_output,
        )


class LlamaCppReplyModel:
    def __init__(self, config: LlamaCppConfig) -> None:
        self._config = config
        self._state = "stopped"
        self._process: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._api_key: str | None = None
        self._lifecycle_guard = asyncio.Lock()
        self._generation_guard = asyncio.Lock()
        self._drain_tasks: list[asyncio.Task[None]] = []
        self._diagnostic_tail: deque[str] = deque(maxlen=80)
        self._recovery_available = True
        self._last_generation_stats: GenerationStats | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def last_generation_stats(self) -> GenerationStats | None:
        return self._last_generation_stats

    @property
    def context_size(self) -> int:
        return self._config.context_size

    async def start(self) -> None:
        async with self._lifecycle_guard:
            if self._state == "ready":
                return
            if self._state == "closing":
                raise LlamaCppNotReadyError("llama.cpp adapter is closing")
            await self._validate_assets()
            last_error: BaseException | None = None
            for attempt in range(2):
                try:
                    await self._launch_once()
                    self._recovery_available = True
                    return
                except asyncio.CancelledError:
                    await self._cleanup_process()
                    raise
                except BaseException as error:
                    last_error = error
                    await self._cleanup_process()
                    if attempt == 0:
                        LOGGER.warning(
                            "llama_start_retry error_type=%s", type(error).__name__
                        )
            self._state = "failed"
            raise LlamaCppStartupError("llama.cpp failed to start after one retry") from last_error

    async def close(self) -> None:
        async with self._lifecycle_guard:
            if self._state == "stopped":
                return
            self._state = "closing"
            await self._cleanup_process()
            self._state = "stopped"

    async def stream_reply(self, request: ReplyRequest) -> AsyncIterator[str]:
        options = GenerationOptions(
            max_tokens=self._config.max_tokens,
            temperature=self._config.temperature,
            top_p=self._config.top_p,
            repeat_penalty=self._config.repeat_penalty,
            seed=self._config.seed,
        )
        async for chunk in self._stream_reply_with_options(request, options):
            yield chunk

    async def stream_reply_with_options(
        self, request: ReplyRequest, options: GenerationOptions
    ) -> AsyncIterator[str]:
        if not isinstance(options, GenerationOptions):
            raise TypeError("options must be GenerationOptions")
        async for chunk in self._stream_reply_with_options(request, options):
            yield chunk

    async def complete_chat(
        self,
        *,
        request_id: str,
        messages: Sequence[Mapping[str, str]],
        options: GenerationOptions,
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id cannot be empty")
        if not isinstance(options, GenerationOptions):
            raise TypeError("options must be GenerationOptions")
        normalized_messages = _normalize_chat_messages(messages)
        normalized_format = (
            None if response_format is None else dict(response_format)
        )
        async with self._generation_guard:
            async with self._lifecycle_guard:
                await self._ensure_ready_for_request()
            while True:
                chunks: list[str] = []
                stream = self._stream_chat_http(
                    request_id=request_id,
                    messages=normalized_messages,
                    options=options,
                    response_format=normalized_format,
                )
                try:
                    async for chunk in stream:
                        chunks.append(chunk)
                except asyncio.CancelledError:
                    raise
                except _RecoverableServerError:
                    if not await self._recover_after_failure(bool(chunks)):
                        raise
                    continue
                finally:
                    await stream.aclose()
                self._recovery_available = True
                text = "".join(chunks)
                if not text.strip():
                    raise LlamaCppProtocolError(
                        "llama.cpp returned empty chat completion"
                    )
                return text

    async def _stream_reply_with_options(
        self, request: ReplyRequest, options: GenerationOptions
    ) -> AsyncIterator[str]:
        async with self._generation_guard:
            async with self._lifecycle_guard:
                await self._ensure_ready_for_request()
            while True:
                emitted = False
                http_stream = self._stream_http(request, options)
                try:
                    async for chunk in http_stream:
                        emitted = True
                        yield chunk
                except asyncio.CancelledError:
                    raise
                except _RecoverableServerError:
                    if not await self._recover_after_failure(emitted):
                        raise
                    continue
                finally:
                    await http_stream.aclose()
                self._recovery_available = True
                return

    async def measure_prompt(self, context: ReplyContext) -> int:
        async with self._generation_guard:
            async with self._lifecycle_guard:
                await self._ensure_ready_for_request()
            client = self._client
            if client is None:
                raise LlamaCppNotReadyError("llama.cpp client is unavailable")
            messages = build_reply_messages(context)
            try:
                applied = await client.post(
                    "/apply-template",
                    json={
                        "messages": messages,
                        "add_generation_prompt": True,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
                if applied.status_code != 200:
                    await applied.aread()
                    raise LlamaCppProtocolError(
                        "llama.cpp prompt template application failed"
                    )
                applied_payload = applied.json()
                prompt = applied_payload.get("prompt")
                if not isinstance(prompt, str):
                    raise LlamaCppProtocolError(
                        "llama.cpp returned an invalid rendered prompt"
                    )
                tokenized = await client.post(
                    "/tokenize",
                    json={
                        "content": prompt,
                        "add_special": False,
                        "parse_special": True,
                    },
                )
                if tokenized.status_code != 200:
                    await tokenized.aread()
                    raise LlamaCppProtocolError("llama.cpp prompt tokenization failed")
                token_payload = tokenized.json()
                tokens = token_payload.get("tokens")
                if not isinstance(tokens, list) or any(
                    not isinstance(token, int) for token in tokens
                ):
                    raise LlamaCppProtocolError(
                        "llama.cpp returned invalid prompt tokens"
                    )
                return len(tokens)
            except LlamaCppProtocolError:
                raise
            except (httpx.HTTPError, ValueError, AttributeError) as error:
                raise LlamaCppProtocolError(
                    "llama.cpp prompt measurement failed"
                ) from error

    async def _recover_after_failure(self, emitted: bool) -> bool:
        async with self._lifecycle_guard:
            if self._state in ("closing", "stopped"):
                return False
            await self._mark_failed()
            if emitted or not self._recovery_available:
                return False
            self._recovery_available = False
            try:
                await self._launch_once()
            except asyncio.CancelledError:
                await self._mark_failed()
                self._recovery_available = True
                raise
            except BaseException as error:
                LOGGER.warning(
                    "llama_recovery_failed error_type=%s", type(error).__name__
                )
                await self._mark_failed()
                return False
            return True

    async def _ensure_ready_for_request(self) -> None:
        if self._state == "ready" and self._process is not None:
            if self._process.returncode is None:
                return
            self._state = "failed"
        if self._state == "failed" and self._recovery_available:
            self._recovery_available = False
            await self._cleanup_process()
            try:
                await self._launch_once()
            except asyncio.CancelledError:
                await self._mark_failed()
                self._recovery_available = True
                raise
            except BaseException:
                await self._mark_failed()
                raise
            return
        raise LlamaCppNotReadyError("llama.cpp adapter is not ready")

    async def _validate_assets(self) -> None:
        config = self._config
        for path, label in (
            (config.server_executable, "server executable"),
            (config.model_path, "model"),
            (config.manifest_path, "manifest"),
        ):
            if not path.is_file():
                raise LlamaCppAssetError(f"{label} does not exist")
        try:
            manifest = json.loads(config.manifest_path.read_text(encoding="utf-8"))
            expected_server = manifest["llama_cpp"]["server_sha256"].lower()
            expected_model = manifest["model"]["sha256"].lower()
            expected_bytes = int(manifest["model"]["bytes"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise LlamaCppAssetError("manifest is invalid") from error
        if config.model_path.stat().st_size != expected_bytes:
            raise LlamaCppAssetError("model size does not match manifest")
        server_hash, model_hash = await asyncio.gather(
            asyncio.to_thread(_sha256, config.server_executable),
            asyncio.to_thread(_sha256, config.model_path),
        )
        if not secrets.compare_digest(server_hash, expected_server):
            raise LlamaCppAssetError("server hash does not match manifest")
        if not secrets.compare_digest(model_hash, expected_model):
            raise LlamaCppAssetError("model hash does not match manifest")

    async def _launch_once(self) -> None:
        await self._cleanup_process()
        if await asyncio.to_thread(_port_is_in_use, self._config.host, self._config.port):
            raise LlamaCppStartupError("configured llama.cpp port is already in use")
        self._state = "starting"
        self._api_key = secrets.token_urlsafe(32)
        args = self._server_args()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = await asyncio.create_subprocess_exec(
            str(self._config.server_executable),
            *args,
            cwd=str(self._config.server_executable.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        if self._process.stdout is not None:
            self._drain_tasks.append(
                asyncio.create_task(self._drain_output(self._process.stdout))
            )
        if self._process.stderr is not None:
            self._drain_tasks.append(
                asyncio.create_task(self._drain_output(self._process.stderr))
            )
        timeout = httpx.Timeout(
            connect=self._config.connect_timeout_seconds,
            read=self._config.read_idle_timeout_seconds,
            write=self._config.read_idle_timeout_seconds,
            pool=self._config.connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=f"http://{self._config.host}:{self._config.port}",
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=timeout,
            trust_env=False,
        )
        await self._wait_until_healthy()
        await self._verify_model_alias()
        warmup_context = initial_reply_context(
            user_event_id="warmup",
            text="只回复：好",
            occurred_at=datetime.now(timezone.utc),
            timezone="UTC",
            context_size=self._config.context_size,
            reply_reserve_tokens=min(256, max(1, self._config.context_size // 8)),
            safety_margin_tokens=min(256, max(1, self._config.context_size // 8)),
        )
        warmup = ReplyRequest(
            "warmup", "warmup", "warmup", "只回复：好", warmup_context
        )
        warmup_options = GenerationOptions(
            max_tokens=4,
            temperature=self._config.temperature,
            top_p=self._config.top_p,
            repeat_penalty=self._config.repeat_penalty,
            seed=self._config.seed,
        )
        async for _chunk in self._stream_http(
            warmup, warmup_options, reject_token_limit=False
        ):
            pass
        self._state = "ready"

    def _server_args(self) -> list[str]:
        config = self._config
        args = [
            *config.server_prefix_args,
            "--model",
            str(config.model_path),
            "--alias",
            config.model_alias,
            "--host",
            config.host,
            "--port",
            str(config.port),
            "--ctx-size",
            str(config.context_size),
            "--parallel",
            str(config.parallel),
            "--batch-size",
            str(config.batch_size),
            "--ubatch-size",
            str(config.ubatch_size),
            "--n-gpu-layers",
            str(config.gpu_layers),
            "--flash-attn",
            "on" if config.flash_attention else "off",
            "--cache-type-k",
            config.kv_cache_k,
            "--cache-type-v",
            config.kv_cache_v,
            "--reasoning-budget",
            "0",
            "--jinja",
            "--no-webui",
            "--log-disable",
            "--api-key",
            self._api_key or "",
        ]
        if config.chat_template is not None:
            args.extend(("--chat-template", config.chat_template))
        return args

    async def _wait_until_healthy(self) -> None:
        if self._client is None or self._process is None:
            raise LlamaCppStartupError("llama.cpp process was not created")
        deadline = asyncio.get_running_loop().time() + self._config.startup_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if self._process.returncode is not None:
                raise LlamaCppStartupError("llama.cpp exited during startup")
            try:
                response = await self._client.get("/health")
                if response.status_code == 200 and response.json().get("status") == "ok":
                    return
                if response.status_code not in (200, 503):
                    raise LlamaCppStartupError("llama.cpp health check failed")
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
                pass
            except (ValueError, AttributeError) as error:
                raise LlamaCppStartupError("llama.cpp returned invalid health data") from error
            await asyncio.sleep(0.1)
        raise LlamaCppStartupError("llama.cpp health check timed out")

    async def _verify_model_alias(self) -> None:
        if self._client is None:
            raise LlamaCppStartupError("llama.cpp client is unavailable")
        try:
            response = await self._client.get("/v1/models")
            response.raise_for_status()
            payload = response.json()
            aliases = {
                item.get("id") or item.get("name")
                for key in ("data", "models")
                for item in payload.get(key, [])
                if isinstance(item, dict)
            }
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            raise LlamaCppStartupError("could not verify llama.cpp model") from error
        if self._config.model_alias not in aliases:
            raise LlamaCppStartupError("llama.cpp loaded an unexpected model")

    async def _stream_http(
        self,
        request: ReplyRequest,
        options: GenerationOptions,
        *,
        reject_token_limit: bool = True,
    ) -> AsyncIterator[str]:
        async for chunk in self._stream_chat_http(
            request_id=request.request_id,
            messages=build_reply_messages(request.context),
            options=options,
            response_format=None,
            reject_token_limit=reject_token_limit,
        ):
            yield chunk

    async def _stream_chat_http(
        self,
        *,
        request_id: str,
        messages: Sequence[Mapping[str, str]],
        options: GenerationOptions,
        response_format: Mapping[str, object] | None,
        reject_token_limit: bool = True,
    ) -> AsyncIterator[str]:
        client = self._client
        if client is None:
            raise LlamaCppNotReadyError("llama.cpp client is unavailable")
        body = {
            "model": self._config.model_alias,
            "messages": [dict(item) for item in messages],
            "temperature": options.temperature,
            "top_p": options.top_p,
            "repeat_penalty": options.repeat_penalty,
            "max_tokens": options.max_tokens,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if options.seed is not None:
            body["seed"] = options.seed
        if response_format is not None:
            if self._config.structured_output != "json_schema":
                raise LlamaCppProtocolError(
                    "model compatibility contract does not support structured output"
                )
            body["response_format"] = dict(response_format)
        if self._config.stop_sequences:
            body["stop"] = list(self._config.stop_sequences)
        if self._config.collect_usage:
            body["stream_options"] = {"include_usage": True}
        filter_state = _ThinkingFilter()
        saw_done = False
        usage: dict[str, object] = {}
        timings: dict[str, object] = {}
        started_at = time.perf_counter()
        first_visible_at: float | None = None
        try:
            async with asyncio.timeout(self._config.generation_timeout_seconds):
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json=body,
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        await response.aread()
                        if response.status_code >= 500:
                            raise _RecoverableServerError(
                                f"llama.cpp returned HTTP {response.status_code}"
                            )
                        raise LlamaCppProtocolError(
                            f"llama.cpp returned HTTP {response.status_code}"
                        )
                    async for data in _iter_sse_data(response):
                        if data == "[DONE]":
                            saw_done = True
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError as error:
                            raise LlamaCppProtocolError("llama.cpp returned malformed SSE JSON") from error
                        if not isinstance(event, dict):
                            raise LlamaCppProtocolError("llama.cpp returned invalid SSE event")
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        if isinstance(event.get("timings"), dict):
                            timings = event["timings"]
                        choices = event.get("choices", [])
                        if not isinstance(choices, list):
                            raise LlamaCppProtocolError("llama.cpp returned invalid choices")
                        for choice in choices:
                            if not isinstance(choice, dict):
                                raise LlamaCppProtocolError("llama.cpp returned invalid choice")
                            if (
                                reject_token_limit
                                and choice.get("finish_reason") == "length"
                            ):
                                raise LlamaCppProtocolError(
                                    "llama.cpp output reached the token limit"
                                )
                            delta = choice.get("delta") or {}
                            if not isinstance(delta, dict):
                                raise LlamaCppProtocolError("llama.cpp returned invalid delta")
                            content = delta.get("content")
                            if content is None:
                                continue
                            if not isinstance(content, str):
                                raise LlamaCppProtocolError("llama.cpp returned non-text content")
                            visible = filter_state.feed(content)
                            if visible:
                                if first_visible_at is None:
                                    first_visible_at = time.perf_counter()
                                yield visible
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise _RecoverableServerError("llama.cpp generation timed out") from error
        except (httpx.TransportError, httpx.StreamError) as error:
            raise _RecoverableServerError("llama.cpp stream failed") from error
        if not saw_done:
            raise _RecoverableServerError("llama.cpp stream ended before DONE")
        tail = filter_state.finish()
        if tail:
            if first_visible_at is None:
                first_visible_at = time.perf_counter()
            yield tail
        self._last_generation_stats = GenerationStats(
            request_id=request_id,
            first_token_seconds=(
                first_visible_at - started_at
                if first_visible_at is not None
                else None
            ),
            prompt_tokens=_optional_int(usage.get("prompt_tokens"))
            or _optional_int(timings.get("prompt_n")),
            generated_tokens=_optional_int(usage.get("completion_tokens"))
            or _optional_int(timings.get("predicted_n")),
            prompt_tokens_per_second=_optional_float(
                timings.get("prompt_per_second")
            ),
            generated_tokens_per_second=_optional_float(
                timings.get("predicted_per_second")
            ),
            elapsed_seconds=time.perf_counter() - started_at,
        )

    async def _mark_failed(self) -> None:
        self._state = "failed"
        await self._cleanup_process()
        self._state = "failed"

    async def _cleanup_process(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=self._config.shutdown_timeout_seconds
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if process is not None:
            await self._wait_for_owned_port_release()
        tasks, self._drain_tasks = self._drain_tasks, []
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._state not in ("closing", "failed"):
            self._state = "stopped"

    async def _wait_for_owned_port_release(self) -> None:
        deadline = (
            asyncio.get_running_loop().time()
            + self._config.shutdown_timeout_seconds
        )
        while asyncio.get_running_loop().time() < deadline:
            if not await asyncio.to_thread(
                _port_is_in_use, self._config.host, self._config.port
            ):
                return
            await asyncio.sleep(0.02)

    async def _drain_output(self, stream: asyncio.StreamReader) -> None:
        while line := await stream.readline():
            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                self._diagnostic_tail.append(decoded[:500])


class _ThinkingFilter:
    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        visible: list[str] = []
        while self._buffer:
            marker = self._CLOSE if self._inside else self._OPEN
            index = self._buffer.find(marker)
            if index >= 0:
                if not self._inside:
                    visible.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(marker) :]
                self._inside = not self._inside
                continue
            keep = _partial_marker_suffix(self._buffer, marker)
            consumable = len(self._buffer) - keep
            if not self._inside:
                visible.append(self._buffer[:consumable])
            self._buffer = self._buffer[consumable:]
            break
        return "".join(visible)

    def finish(self) -> str:
        if self._inside:
            self._buffer = ""
            return ""
        value, self._buffer = self._buffer, ""
        return value


async def _iter_sse_data(response: httpx.Response) -> AsyncIterator[str]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if data_lines:
        yield "\n".join(data_lines)


def _partial_marker_suffix(value: str, marker: str) -> int:
    maximum = min(len(value), len(marker) - 1)
    for size in range(maximum, 0, -1):
        if marker.startswith(value[-size:]):
            return size
    return 0


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return int(value) if isinstance(value, (int, float)) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _normalize_chat_messages(
    messages: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    if isinstance(messages, (str, bytes)):
        raise TypeError("messages must be a sequence of mappings")
    normalized: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise TypeError(f"messages[{index}] must be a mapping")
        if set(message) != {"role", "content"}:
            raise ValueError(
                f"messages[{index}] must contain only role and content"
            )
        role = message["role"]
        content = message["content"]
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"messages[{index}].role is invalid")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"messages[{index}].content cannot be empty")
        normalized.append({"role": role, "content": content})
    if not normalized:
        raise ValueError("messages cannot be empty")
    return tuple(normalized)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _port_is_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((host, port)) == 0
