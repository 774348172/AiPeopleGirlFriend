from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from runtime._real_assets import (
    LocalRealAssetBundle,
    LocalRetrievalAssetBundle,
    RealAssetError,
)
from runtime.adapters.llama_cpp import LlamaCppConfig, LlamaCppReplyModel
from runtime.adapters.local_bge import BgeBackend, ManifestBgeEncoder
from runtime.adapters.qwen3_reranker import LocalQwen3Reranker, RerankerBackend

from .memory_repository import HeroineMemoryRepositoryFactory
from .memory_proposer import LlamaCppHeroineMemoryProposer
from .persistence import WorldMindStore
from .real_model_gateway import (
    LlamaCppWorldMindModel,
    WorldMindModelIdentity,
)
from .settings import WorldMindRuntimeConfig


@dataclass(frozen=True, slots=True)
class RealWorldMindStack:
    assets: LocalRealAssetBundle
    encoder: ManifestBgeEncoder
    reranker: LocalQwen3Reranker
    memory_repository_factory: HeroineMemoryRepositoryFactory
    llama_backend: LlamaCppReplyModel
    world_mind_model: LlamaCppWorldMindModel
    memory_proposer: LlamaCppHeroineMemoryProposer


@dataclass(frozen=True, slots=True)
class RealRetrievalStack:
    assets: LocalRetrievalAssetBundle
    encoder: ManifestBgeEncoder
    reranker: LocalQwen3Reranker
    memory_repository_factory: HeroineMemoryRepositoryFactory


def build_real_retrieval_stack(
    *,
    store: WorldMindStore,
    assets: LocalRetrievalAssetBundle,
    bge_loader: Callable[[object], BgeBackend] | None = None,
    reranker_loader: Callable[[object], RerankerBackend] | None = None,
) -> RealRetrievalStack:
    if not isinstance(store, WorldMindStore):
        raise TypeError("store must be WorldMindStore")
    if not isinstance(assets, LocalRetrievalAssetBundle):
        raise TypeError("assets must be LocalRetrievalAssetBundle")
    encoder = ManifestBgeEncoder(assets.bge, loader=bge_loader)
    reranker = LocalQwen3Reranker(assets.reranker, loader=reranker_loader)
    factory = HeroineMemoryRepositoryFactory(
        store,
        encoder=encoder,
        reranker=reranker,
    )
    return RealRetrievalStack(
        assets=assets,
        encoder=encoder,
        reranker=reranker,
        memory_repository_factory=factory,
    )


def build_real_world_mind_stack(
    *,
    store: WorldMindStore,
    assets: LocalRealAssetBundle,
    llama_config: LlamaCppConfig,
    world_mind_config: WorldMindRuntimeConfig,
    character_id: str,
    bge_loader: Callable[[object], BgeBackend] | None = None,
    reranker_loader: Callable[[object], RerankerBackend] | None = None,
) -> RealWorldMindStack:
    if not isinstance(store, WorldMindStore):
        raise TypeError("store must be WorldMindStore")
    package = world_mind_config.character_package(character_id)
    identity = assets.reply.identity
    if identity.model_role != "heroine_reply":
        raise RealAssetError("world-mind stack requires a heroine_reply package")
    if identity.character_id != package.manifest.character_id:
        raise RealAssetError(
            "reply package character_id does not match character package"
        )
    if identity.world_id != world_mind_config.expected_world_id:
        raise RealAssetError(
            "reply package world_id does not match V6 runtime config"
        )
    if identity.protagonist_id != world_mind_config.expected_protagonist_id:
        raise RealAssetError(
            "reply package protagonist_id does not match V6 runtime config"
        )
    if llama_config.model_path.resolve() != assets.reply.gguf_path.resolve():
        raise RealAssetError(
            "llama.cpp model path is not bound to the reply package GGUF"
        )
    if (
        llama_config.manifest_path.resolve()
        != assets.llama_runtime_manifest.resolve()
    ):
        raise RealAssetError(
            "llama.cpp config is not bound to the bundle runtime manifest"
        )
    encoder = ManifestBgeEncoder(assets.bge, loader=bge_loader)
    reranker = LocalQwen3Reranker(assets.reranker, loader=reranker_loader)
    memory_factory = HeroineMemoryRepositoryFactory(
        store,
        encoder=encoder,
        reranker=reranker,
    )
    backend = LlamaCppReplyModel(llama_config)
    model_identity = WorldMindModelIdentity(
        model_id=identity.model_id,
        revision=identity.revision,
        artifact_sha256=identity.artifact_sha256,
        character_id=identity.character_id,
        world_id=identity.world_id,
        protagonist_id=identity.protagonist_id,
    )
    model = LlamaCppWorldMindModel(
        backend,
        identity=model_identity,
    )
    memory_proposer = LlamaCppHeroineMemoryProposer(
        backend,
        identity=model_identity,
        character_prompt=f"当前女主角：{package.manifest.display_name}",
    )
    return RealWorldMindStack(
        assets=assets,
        encoder=encoder,
        reranker=reranker,
        memory_repository_factory=memory_factory,
        llama_backend=backend,
        world_mind_model=model,
        memory_proposer=memory_proposer,
    )
