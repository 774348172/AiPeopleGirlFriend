from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Sequence
from typing import Protocol

from runtime._recall_candidates import GlobalRecallTop32
from runtime._reranker import (
    RERANK_MAX_PAIR_TOKENS,
    RerankResult,
    RerankScore,
    RerankerIdentity,
    render_reranker_pair,
)
from runtime._reranker_assets import LocalRerankerPackage, RerankerAssetError


class RerankerBackend(Protocol):
    def score_pairs(self, pairs: Sequence[str], *, maximum_tokens: int) -> Sequence[float]: ...

    def close(self) -> None: ...


class LocalQwen3Reranker:
    """Offline batched Qwen3 reranker adapter with an injectable backend."""

    def __init__(
        self,
        package: LocalRerankerPackage,
        *,
        loader: Callable[[LocalRerankerPackage], RerankerBackend] | None = None,
    ) -> None:
        if not isinstance(package, LocalRerankerPackage):
            raise TypeError("package must be LocalRerankerPackage")
        self._package = package
        self._loader = loader or _load_transformers_backend
        self._backend: RerankerBackend | None = None

    @property
    def identity(self) -> RerankerIdentity:
        return self._package.identity

    @property
    def activation_threshold(self) -> float:
        return self._package.activation_threshold

    async def start(self) -> None:
        if self._backend is None:
            self._backend = await asyncio.to_thread(self._loader, self._package)

    async def validate_runtime(self) -> None:
        await self.start()
        assert self._backend is not None
        values = await asyncio.to_thread(
            self._backend.score_pairs,
            ("query=雨夜\nmemory=男主喜欢雨夜",),
            maximum_tokens=RERANK_MAX_PAIR_TOKENS,
        )
        if len(values) != 1 or not math.isfinite(float(values[0])):
            raise RerankerAssetError("reranker runtime self-test failed")

    async def close(self) -> None:
        backend, self._backend = self._backend, None
        if backend is not None:
            await asyncio.to_thread(backend.close)

    async def rerank(self, batch: GlobalRecallTop32) -> RerankResult:
        if self._backend is None:
            raise RuntimeError("reranker adapter has not been started")
        pairs = tuple(render_reranker_pair(batch, item) for item in batch.candidates)
        raw_scores = await asyncio.to_thread(
            self._backend.score_pairs,
            pairs,
            maximum_tokens=RERANK_MAX_PAIR_TOKENS,
        )
        if len(raw_scores) != len(batch.candidates):
            raise RuntimeError("reranker backend returned the wrong score count")
        scores = tuple(
            RerankScore(memory_id=item.memory_id, activation_score=float(score))
            for item, score in zip(batch.candidates, raw_scores, strict=True)
        )
        return RerankResult(
            identity=self.identity,
            threshold=self.activation_threshold,
            scores=scores,
            relative_top_floor=self._package.relative_top_floor,
            relative_top_margin=self._package.relative_top_margin,
        )


class _TransformersBackend:
    def __init__(self, package: LocalRerankerPackage) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        except ImportError as error:
            raise RerankerAssetError("torch and transformers are required for reranking") from error
        profile = package.identity.deployment_profile
        kwargs: dict[str, object] = {
            "local_files_only": True,
            "trust_remote_code": False,
        }
        target_device = "cuda"
        if profile == "cpu-fp32":
            kwargs["dtype"] = torch.float32
            target_device = "cpu"
        elif not torch.cuda.is_available():
            raise RerankerAssetError("selected Qwen3 reranker profile requires a CUDA GPU")
        elif profile == "cuda-bf16":
            kwargs["dtype"] = torch.bfloat16
        elif profile == "cuda-fp16":
            kwargs["dtype"] = torch.float16
        elif profile == "cuda-int8":
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
            kwargs["device_map"] = "cuda:0"
        elif profile == "cuda-nf4":
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
            )
            kwargs["device_map"] = "cuda:0"
        else:
            raise RerankerAssetError(f"unsupported reranker deployment_profile: {profile}")
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(package.root), local_files_only=True, trust_remote_code=False
        )
        self._model = AutoModelForCausalLM.from_pretrained(str(package.root), **kwargs)
        if "device_map" not in kwargs:
            self._model.to(target_device)
        self._model.eval()
        self._yes_token = self._single_token("yes")
        self._no_token = self._single_token("no")
        self._prefix = (
            '<|im_start|>system\nJudge whether the Document meets the requirements '
            'based on the Query and the Instruct provided. Note that the answer can '
            'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
        )
        self._suffix = (
            "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )

    def score_pairs(self, pairs: Sequence[str], *, maximum_tokens: int) -> Sequence[float]:
        prompts = [f"{self._prefix}{pair}{self._suffix}" for pair in pairs]
        encoded = self._tokenizer(
            prompts,
            padding=True,
            truncation=True,
            max_length=maximum_tokens,
            return_tensors="pt",
        )
        device = next(self._model.parameters()).device
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with self._torch.inference_mode():
            logits = self._model(**encoded).logits
        final = logits[:, -1, :]
        binary = final[:, [self._no_token, self._yes_token]].float()
        probabilities = self._torch.softmax(binary, dim=-1)[:, 1]
        values = probabilities.detach().cpu().tolist()
        if any(not math.isfinite(float(value)) for value in values):
            raise RuntimeError("reranker backend produced non-finite probabilities")
        return values

    def close(self) -> None:
        self._model = None
        if self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()

    def _single_token(self, value: str) -> int:
        token = self._tokenizer.convert_tokens_to_ids(value)
        if not isinstance(token, int) or token < 0 or token == self._tokenizer.unk_token_id:
            raise RerankerAssetError(f"reranker label must be one token: {value}")
        return token


def _load_transformers_backend(package: LocalRerankerPackage) -> RerankerBackend:
    return _TransformersBackend(package)
