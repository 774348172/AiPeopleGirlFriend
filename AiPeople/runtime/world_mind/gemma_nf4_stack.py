from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from runtime._real_assets import LocalRetrievalAssetBundle, RealAssetError
from runtime.adapters.gemma_nf4 import (
    GemmaNF4Config,
    GemmaNF4WorldMindBackend,
    GenerationRunner,
    ModelLoader,
)
from runtime.adapters.local_bge import BgeBackend, ManifestBgeEncoder
from runtime.adapters.qwen3_reranker import LocalQwen3Reranker, RerankerBackend
from runtime.gemma_nf4_assets import LocalGemmaNF4Package

from .memory_proposer import LlamaCppHeroineMemoryProposer
from .memory_repository import HeroineMemoryRepositoryFactory
from .persistence import WorldMindStore
from .real_model_gateway import (
    LlamaCppWorldMindModel,
    WorldMindModeProfile,
    WorldMindModelIdentity,
)
from .settings import WorldMindRuntimeConfig


@dataclass(frozen=True, slots=True)
class GemmaNF4WorldMindStack:
    retrieval_assets: LocalRetrievalAssetBundle
    reply_asset: LocalGemmaNF4Package
    encoder: ManifestBgeEncoder
    reranker: LocalQwen3Reranker
    memory_repository_factory: HeroineMemoryRepositoryFactory
    backend: GemmaNF4WorldMindBackend
    world_mind_model: LlamaCppWorldMindModel
    memory_proposer: LlamaCppHeroineMemoryProposer


def build_gemma_nf4_world_mind_stack(
    *,
    store: WorldMindStore,
    retrieval_assets: LocalRetrievalAssetBundle,
    reply_asset: LocalGemmaNF4Package,
    world_mind_config: WorldMindRuntimeConfig,
    character_id: str,
    bge_loader: Callable[[object], BgeBackend] | None = None,
    reranker_loader: Callable[[object], RerankerBackend] | None = None,
    gemma_loader: ModelLoader | None = None,
    generation_runner: GenerationRunner | None = None,
    mode_profiles: Mapping[str, WorldMindModeProfile] | None = None,
) -> GemmaNF4WorldMindStack:
    if not isinstance(store, WorldMindStore):
        raise TypeError("store must be WorldMindStore")
    if not isinstance(retrieval_assets, LocalRetrievalAssetBundle):
        raise TypeError("retrieval_assets must be LocalRetrievalAssetBundle")
    if not isinstance(reply_asset, LocalGemmaNF4Package):
        raise TypeError("reply_asset must be LocalGemmaNF4Package")
    package = world_mind_config.character_package(character_id)
    identity = reply_asset.identity
    if identity.model_role != "heroine_reply":
        raise RealAssetError("Gemma NF4 stack requires a heroine_reply package")
    if identity.character_id != package.manifest.character_id:
        raise RealAssetError(
            "Gemma NF4 character_id does not match the character package"
        )
    if identity.world_id != world_mind_config.expected_world_id:
        raise RealAssetError("Gemma NF4 world_id does not match runtime config")
    if identity.protagonist_id != world_mind_config.expected_protagonist_id:
        raise RealAssetError(
            "Gemma NF4 protagonist_id does not match runtime config"
        )

    encoder = ManifestBgeEncoder(retrieval_assets.bge, loader=bge_loader)
    reranker = LocalQwen3Reranker(
        retrieval_assets.reranker, loader=reranker_loader
    )
    memory_factory = HeroineMemoryRepositoryFactory(
        store,
        encoder=encoder,
        reranker=reranker,
    )
    backend = GemmaNF4WorldMindBackend(
        GemmaNF4Config(reply_asset),
        loader=gemma_loader,
        generation_runner=generation_runner,
    )
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
        mode_profiles=mode_profiles,
    )
    memory_proposer = LlamaCppHeroineMemoryProposer(
        backend,
        identity=model_identity,
        character_prompt=f"当前女主角：{package.manifest.display_name}",
    )
    return GemmaNF4WorldMindStack(
        retrieval_assets=retrieval_assets,
        reply_asset=reply_asset,
        encoder=encoder,
        reranker=reranker,
        memory_repository_factory=memory_factory,
        backend=backend,
        world_mind_model=model,
        memory_proposer=memory_proposer,
    )
