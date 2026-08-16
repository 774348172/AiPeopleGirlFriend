from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ._memory_aware_model import MemoryAwareReplyModel
from ._memory_selection import MemorySelectionPipeline
from ._memory_store import MemoryStore
from ._memory_vectors import MemoryVectorStore
from ._real_assets import LocalRealAssetBundle, RealAssetError
from ._selected_memory import SelectedMemoryAssembler
from .adapters.llama_cpp import LlamaCppConfig, LlamaCppReplyModel
from .adapters.local_bge import BgeBackend, ManifestBgeEncoder
from .adapters.qwen3_reranker import LocalQwen3Reranker, RerankerBackend
from .world_mind import WorldMindRuntimeConfig


@dataclass(frozen=True, slots=True)
class RealMemoryStack:
    assets: LocalRealAssetBundle
    encoder: ManifestBgeEncoder
    vector_store: MemoryVectorStore
    reranker: LocalQwen3Reranker
    selector: MemorySelectionPipeline
    reply_model: LlamaCppReplyModel
    memory_aware_reply_model: MemoryAwareReplyModel


def build_real_memory_stack(
    *,
    connection: sqlite3.Connection,
    memory_store: MemoryStore,
    assets: LocalRealAssetBundle,
    llama_config: LlamaCppConfig,
    world_mind_config: WorldMindRuntimeConfig,
    character_id: str,
    bge_loader: Callable[[object], BgeBackend] | None = None,
    reranker_loader: Callable[[object], RerankerBackend] | None = None,
    rerank_timeout_seconds: float = 0.25,
) -> RealMemoryStack:
    package = world_mind_config.character_package(character_id)
    identity = assets.reply.identity
    if identity.model_role != "heroine_reply":
        raise RealAssetError("online stack requires a heroine_reply package")
    if identity.character_id != package.manifest.character_id:
        raise RealAssetError("reply package character_id does not match character package")
    if identity.world_id != world_mind_config.expected_world_id:
        raise RealAssetError("reply package world_id does not match V6 runtime config")
    if identity.protagonist_id != world_mind_config.expected_protagonist_id:
        raise RealAssetError(
            "reply package protagonist_id does not match V6 runtime config"
        )
    if llama_config.model_path.resolve() != assets.reply.gguf_path.resolve():
        raise RealAssetError("llama.cpp model path is not bound to the reply package GGUF")
    if llama_config.manifest_path.resolve() != assets.llama_runtime_manifest.resolve():
        raise RealAssetError("llama.cpp config is not bound to the bundle runtime manifest")
    encoder = ManifestBgeEncoder(assets.bge, loader=bge_loader)
    vector_store = MemoryVectorStore(connection, encoder)
    reranker = LocalQwen3Reranker(assets.reranker, loader=reranker_loader)
    selector = MemorySelectionPipeline(
        vector_store=vector_store,
        query_encoder=encoder,
        query_tokenizer=encoder,
        reranker=reranker,
        rerank_timeout_seconds=rerank_timeout_seconds,
    )
    reply_model = LlamaCppReplyModel(llama_config)
    wrapper = MemoryAwareReplyModel(
        reply_model,
        selector,
        SelectedMemoryAssembler.from_memory_store(memory_store),
    )
    return RealMemoryStack(
        assets=assets,
        encoder=encoder,
        vector_store=vector_store,
        reranker=reranker,
        selector=selector,
        reply_model=reply_model,
        memory_aware_reply_model=wrapper,
    )
