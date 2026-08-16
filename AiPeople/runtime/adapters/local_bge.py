from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from runtime._memory_vectors import EncoderIdentity
from runtime._memory_vectors import BGE_DIMENSION
from runtime._real_assets import LocalBgePackage, RealAssetError


class BgeBackend(Protocol):
    def encode(self, texts: list[str], **kwargs): ...


class ManifestBgeEncoder:
    """Offline CPU adapter bound to a verified BGE package manifest."""

    def __init__(
        self,
        package: LocalBgePackage,
        *,
        loader: Callable[[LocalBgePackage], BgeBackend] | None = None,
    ) -> None:
        if not isinstance(package, LocalBgePackage):
            raise TypeError("package must be LocalBgePackage")
        self._package = package
        self._loader = loader or _load_transformers_bge
        self._backend: BgeBackend | None = None

    @property
    def identity(self) -> EncoderIdentity:
        return self._package.identity

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        batch = list(texts)
        if not batch:
            return ()
        backend = self._ensure_backend()
        rows = backend.encode(
            batch,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=False,
            normalize_embeddings=False,
        )
        if len(rows) != len(batch):
            raise RealAssetError("BGE backend returned the wrong row count")
        checked = []
        for row in rows:
            values = tuple(float(value) for value in row)
            if len(values) != BGE_DIMENSION:
                raise RealAssetError("BGE backend returned the wrong dimension")
            checked.append(values)
        return tuple(checked)

    def validate_runtime(self) -> None:
        rows = self.encode(("白未晞记得男主喜欢雨夜。", "男主现在正在吃面。"))
        if len(rows) != 2:
            raise RealAssetError("BGE runtime self-test failed")

    def count_tokens(self, text: str) -> int:
        if not isinstance(text, str):
            raise TypeError("BGE token measurement input must be a string")
        backend = self._ensure_backend()
        tokenizer = getattr(backend, "tokenizer", None)
        encode = getattr(tokenizer, "encode", None)
        if not callable(encode):
            raise RealAssetError("BGE backend tokenizer does not provide encode()")
        tokens = encode(text, add_special_tokens=True, truncation=False)
        if not isinstance(tokens, list):
            raise RealAssetError("BGE tokenizer returned an invalid token sequence")
        return len(tokens)

    def _ensure_backend(self) -> BgeBackend:
        if self._backend is None:
            self._backend = self._loader(self._package)
        return self._backend


class _TransformersBgeBackend:
    def __init__(self, package: LocalBgePackage) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as error:
            raise RealAssetError(
                "torch and transformers are required for the delivered BGE package"
            ) from error
        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(package.root), local_files_only=True, trust_remote_code=False
        )
        self._model = AutoModel.from_pretrained(
            str(package.root),
            local_files_only=True,
            trust_remote_code=False,
        ).to("cpu")
        self._model.eval()

    def encode(self, texts: list[str], **kwargs):
        batch_size = int(kwargs.get("batch_size", 32))
        rows = []
        for start in range(0, len(texts), batch_size):
            encoded = self.tokenizer(
                texts[start : start + batch_size],
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            with self._torch.inference_mode():
                hidden = self._model(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1)
            rows.extend(pooled.float().cpu().tolist())
        return rows


def _load_transformers_bge(package: LocalBgePackage) -> BgeBackend:
    return _TransformersBgeBackend(package)
