from __future__ import annotations

import asyncio
import gc
import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from runtime.gemma_nf4_assets import LocalGemmaNF4Package

from .llama_cpp import GenerationOptions


class GemmaNF4Error(RuntimeError):
    pass


class GemmaNF4NotReadyError(GemmaNF4Error):
    pass


class GemmaNF4ProtocolError(GemmaNF4Error):
    pass


@dataclass(frozen=True, slots=True)
class GemmaNF4Config:
    asset: LocalGemmaNF4Package
    request_timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        if not isinstance(self.asset, LocalGemmaNF4Package):
            raise TypeError("asset must be LocalGemmaNF4Package")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class GemmaNF4GenerationMetrics:
    request_id: str
    first_visible_ms: float | None
    total_ms: float
    prompt_tokens: int
    generated_tokens: int
    tokens_per_second: float


@dataclass(frozen=True, slots=True)
class GemmaNF4GenerationResult:
    text: str
    first_visible_ms: float | None
    prompt_tokens: int
    generated_tokens: int


ModelLoader = Callable[[GemmaNF4Config], tuple[Any, Any]]
GenerationRunner = Callable[
    [Any, Any, Sequence[Mapping[str, str]], GenerationOptions],
    GemmaNF4GenerationResult,
]


class GemmaNF4WorldMindBackend:
    def __init__(
        self,
        config: GemmaNF4Config,
        *,
        loader: ModelLoader | None = None,
        generation_runner: GenerationRunner | None = None,
    ) -> None:
        if not isinstance(config, GemmaNF4Config):
            raise TypeError("config must be GemmaNF4Config")
        self.config = config
        self._loader = loader or _load_model
        self._generation_runner = generation_runner or _generate
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._generation_lock = asyncio.Lock()
        self._drain_tasks: set[asyncio.Task[None]] = set()
        self.last_generation_metrics: GemmaNF4GenerationMetrics | None = None

    @property
    def state(self) -> str:
        return "ready" if self._model is not None else "stopped"

    @property
    def context_size(self) -> int:
        return self.config.asset.runtime.max_seq_length

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._model is not None:
                return
            try:
                model, tokenizer = await asyncio.to_thread(self._loader, self.config)
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                raise GemmaNF4NotReadyError("Gemma NF4 model failed to load") from error
            self._model = model
            self._tokenizer = tokenizer

    async def close(self) -> None:
        async with self._generation_lock:
            async with self._lifecycle_lock:
                self._model = None
                self._tokenizer = None
                self.last_generation_metrics = None
                await asyncio.to_thread(_release_cuda)

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
        normalized = _normalize_messages(messages)
        prepared = _with_json_schema(normalized, response_format)
        await self._generation_lock.acquire()
        release_generation_lock = True
        try:
            model = self._model
            tokenizer = self._tokenizer
            if model is None or tokenizer is None:
                raise GemmaNF4NotReadyError("Gemma NF4 backend is not started")
            started = time.perf_counter()
            runner_task = asyncio.create_task(
                asyncio.to_thread(
                    self._generation_runner,
                    model,
                    tokenizer,
                    prepared,
                    options,
                )
            )
            try:
                async with asyncio.timeout(self.config.request_timeout_seconds):
                    result = await asyncio.shield(runner_task)
            except asyncio.CancelledError:
                self._release_lock_after_runner(runner_task)
                release_generation_lock = False
                raise
            except TimeoutError as error:
                self._release_lock_after_runner(runner_task)
                release_generation_lock = False
                raise GemmaNF4Error("Gemma NF4 generation timed out") from error
            except GemmaNF4Error:
                raise
            except BaseException as error:
                raise GemmaNF4Error("Gemma NF4 generation failed") from error
            text = result.text.strip()
            if not text:
                raise GemmaNF4ProtocolError("Gemma NF4 returned an empty response")
            total_ms = (time.perf_counter() - started) * 1000
            self.last_generation_metrics = GemmaNF4GenerationMetrics(
                request_id=request_id,
                first_visible_ms=result.first_visible_ms,
                total_ms=round(total_ms, 2),
                prompt_tokens=result.prompt_tokens,
                generated_tokens=result.generated_tokens,
                tokens_per_second=round(
                    result.generated_tokens / max(total_ms / 1000, 1e-9), 2
                ),
            )
            return text
        finally:
            if release_generation_lock:
                self._generation_lock.release()

    def _release_lock_after_runner(self, runner_task: asyncio.Task[Any]) -> None:
        async def drain() -> None:
            try:
                await runner_task
            except BaseException:
                pass
            finally:
                self._generation_lock.release()

        task = asyncio.create_task(drain())
        self._drain_tasks.add(task)
        task.add_done_callback(self._drain_tasks.discard)


def _load_model(config: GemmaNF4Config) -> tuple[Any, Any]:
    import torch
    from unsloth import FastModel
    from unsloth.chat_templates import get_chat_template

    asset = config.asset
    if not torch.cuda.is_available():
        raise GemmaNF4NotReadyError("Gemma NF4 requires a CUDA GPU")
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(asset.adapter_root),
        max_seq_length=asset.runtime.max_seq_length,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        attn_implementation=asset.runtime.attention_implementation,
    )
    if not getattr(model, "peft_config", None):
        raise GemmaNF4NotReadyError(
            "Gemma NF4 loaded without the required Baiweixi LoRA Adapter"
        )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    return model, tokenizer


def _generate(
    model: Any,
    tokenizer: Any,
    messages: Sequence[Mapping[str, str]],
    options: GenerationOptions,
) -> GemmaNF4GenerationResult:
    import torch

    if options.seed is not None:
        torch.manual_seed(options.seed)
        torch.cuda.manual_seed_all(options.seed)
    encoded = tokenizer.apply_chat_template(
        list(messages),
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_tensors="pt",
        return_dict=True,
    )
    encoded = {key: value.to("cuda") for key, value in encoded.items()}
    prompt_tokens = int(encoded["input_ids"].shape[-1])
    probe = _FirstVisibleProbe(tokenizer)
    kwargs: dict[str, Any] = {
        **encoded,
        "max_new_tokens": options.max_tokens,
        "do_sample": options.temperature > 0,
        "repetition_penalty": options.repeat_penalty,
        "pad_token_id": tokenizer.pad_token_id,
        "eos_token_id": list(
            dict.fromkeys(
                token_id
                for token_id in (tokenizer.eos_token_id, tokenizer.eot_token_id)
                if token_id is not None
            )
        ),
        "use_cache": True,
        "streamer": probe,
    }
    if options.temperature > 0:
        kwargs["temperature"] = options.temperature
        kwargs["top_p"] = options.top_p
    with torch.inference_mode():
        output = model.generate(**kwargs)
    generated_ids = output[0, prompt_tokens:]
    parsed = tokenizer.parse_response(generated_ids)
    response = str(parsed.get("content") or "").strip()
    if not response:
        response = tokenizer.decode(generated_ids, skip_special_tokens=True).strip()
    return GemmaNF4GenerationResult(
        text=response,
        first_visible_ms=probe.first_visible_ms,
        prompt_tokens=prompt_tokens,
        generated_tokens=int(generated_ids.shape[-1]),
    )


class _FirstVisibleProbe:
    def __init__(self, tokenizer: Any) -> None:
        self.tokenizer = tokenizer
        self.started = time.perf_counter()
        self.seen_prompt = False
        self.first_visible_ms: float | None = None
        self._lock = threading.Lock()

    def put(self, value: Any) -> None:
        with self._lock:
            if not self.seen_prompt:
                self.seen_prompt = True
                return
            if self.first_visible_ms is not None:
                return
            token_ids = value.detach().cpu().reshape(-1).tolist()
            text = self.tokenizer.decode(token_ids, skip_special_tokens=True)
            if text.strip():
                self.first_visible_ms = round(
                    (time.perf_counter() - self.started) * 1000, 2
                )

    def end(self) -> None:
        return None


def _normalize_messages(
    messages: Sequence[Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise ValueError("Gemma NF4 messages contain an unsupported role")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Gemma NF4 message content cannot be empty")
        normalized.append({"role": role, "content": content})
    if not normalized:
        raise ValueError("Gemma NF4 messages cannot be empty")
    return tuple(normalized)


def _with_json_schema(
    messages: tuple[dict[str, str], ...],
    response_format: Mapping[str, object] | None,
) -> tuple[dict[str, str], ...]:
    if response_format is None:
        return messages
    try:
        json_schema = response_format["json_schema"]
        schema = json_schema["schema"]  # type: ignore[index]
    except (KeyError, TypeError) as error:
        raise ValueError("Gemma NF4 response_format must contain a JSON schema") from error
    suffix = (
        "\n\n[机器可读输出约束]\n"
        "只输出一个符合下列 JSON Schema 的 JSON 对象，不输出代码围栏、说明或额外文本。\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    prepared = [dict(message) for message in messages]
    if prepared[0]["role"] == "system":
        prepared[0]["content"] += suffix
    else:
        prepared.insert(0, {"role": "system", "content": suffix.strip()})
    return tuple(prepared)


def _release_cuda() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except (ImportError, RuntimeError):
        return
