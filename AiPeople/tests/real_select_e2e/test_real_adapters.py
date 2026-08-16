from __future__ import annotations

from pathlib import Path

import pytest

from runtime._real_assets import LocalBgePackage
from runtime._real_assets import LocalRealAssetBundle, RealAssetError
from runtime._real_stack import build_real_memory_stack
from runtime._ledger import EventLedger
from runtime._recall_candidates import GlobalRecallTop32, Top32Candidate
from runtime._reranker_assets import LocalRerankerPackage
from runtime.adapters.local_bge import ManifestBgeEncoder
from runtime.adapters.qwen3_reranker import LocalQwen3Reranker
from runtime.adapters.llama_cpp import LlamaCppConfig
from runtime.world_mind import WorldMindRuntimeConfig
from tests.real_select_e2e._assets import make_bge_package, make_bundle, make_reranker_package


class Tokenizer:
    def encode(self, text, **kwargs):
        return list(range(max(1, len(text))))


class BgeBackend:
    tokenizer = Tokenizer()

    def encode(self, texts, **kwargs):
        return [[float(index + 1) for index in range(512)] for _ in texts]


class RerankerBackend:
    def __init__(self) -> None:
        self.closed = False
        self.calls = []

    def score_pairs(self, pairs, *, maximum_tokens):
        self.calls.append((tuple(pairs), maximum_tokens))
        return [0.9, 0.1]

    def close(self):
        self.closed = True


def candidate(memory_id: str, rank: int) -> Top32Candidate:
    return Top32Candidate(
        rank=rank,
        memory_id=memory_id,
        memory_version=1,
        coarse_score=0.5,
        memory_kind="shared_experience",
        statement=f"statement for {memory_id}",
        subject_type="both",
        subject_entity_id=None,
        subject_display_name=None,
        temporal_relation="past",
        temporal_source_text="上周",
        temporal_start_at=None,
        temporal_end_at=None,
        temporal_timezone="Asia/Shanghai",
        epistemic_polarity="affirmed",
        epistemic_modality="asserted",
        winning_view_kind="statement",
        winning_view_text=f"statement for {memory_id}",
        source_revision="a" * 64,
    )


def batch() -> GlobalRecallTop32:
    return GlobalRecallTop32(
        query_sha256="1" * 64,
        rendered_query="current conversation",
        query_encoder_revision="bge-r1",
        query_encoder_artifact_sha256="2" * 64,
        generation_id="generation-1",
        global_pool_memory_count=40,
        global_pool_view_count=80,
        candidates=(candidate("memory-a", 1), candidate("memory-b", 2)),
    )


def world_mind_config() -> WorldMindRuntimeConfig:
    root = Path(__file__).resolve().parents[2]
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=root / "世界设定" / "松江府",
        protagonist_canon_dir=root / "人物设定" / "主角",
        character_package_dirs={"baiweixi": root / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
        foreground_protocol="legacy_v1",
    )


def test_manifest_bge_encoder_uses_injected_offline_backend(tmp_path) -> None:
    package = LocalBgePackage.load(make_bge_package(tmp_path / "bge"))
    encoder = ManifestBgeEncoder(package, loader=lambda _: BgeBackend())
    assert encoder.count_tokens("测试") == 2
    assert len(encoder.encode(("一", "二"))) == 2
    assert encoder.identity == package.identity


async def test_qwen_adapter_batches_top32_and_returns_exact_id_scores(tmp_path) -> None:
    package = LocalRerankerPackage.load(make_reranker_package(tmp_path / "reranker"))
    backend = RerankerBackend()
    reranker = LocalQwen3Reranker(package, loader=lambda _: backend)
    await reranker.start()
    result = await reranker.rerank(batch())
    assert [(item.memory_id, item.activation_score) for item in result.scores] == [
        ("memory-a", 0.9),
        ("memory-b", 0.1),
    ]
    assert backend.calls[0][1] == 128
    assert len(backend.calls[0][0]) == 2
    await reranker.close()
    assert backend.closed is True


def test_real_stack_factory_binds_verified_reply_gguf_and_runtime_manifest(tmp_path) -> None:
    bundle_path, _, _, reply_root = make_bundle(tmp_path / "assets")
    assets = LocalRealAssetBundle.load(bundle_path)
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"server")
    config = LlamaCppConfig(
        server_executable=server.resolve(),
        model_path=(reply_root / "model.gguf").resolve(),
        manifest_path=assets.llama_runtime_manifest,
    )
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        store = ledger.memory_store()
        stack = build_real_memory_stack(
            connection=store._connection,
            memory_store=store,
            assets=assets,
            llama_config=config,
            world_mind_config=world_mind_config(),
            character_id="baiweixi",
            bge_loader=lambda _: BgeBackend(),
            reranker_loader=lambda _: RerankerBackend(),
        )
        assert stack.assets == assets
        assert stack.encoder.identity == assets.bge.identity
        assert stack.reranker.identity == assets.reranker.identity
    finally:
        ledger.close()


def test_real_stack_factory_rejects_reply_path_drift(tmp_path) -> None:
    bundle_path, _, _, _ = make_bundle(tmp_path / "assets")
    assets = LocalRealAssetBundle.load(bundle_path)
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"server")
    wrong_model = tmp_path / "wrong.gguf"
    wrong_model.write_bytes(b"wrong")
    config = LlamaCppConfig(
        server_executable=server.resolve(),
        model_path=wrong_model.resolve(),
        manifest_path=assets.llama_runtime_manifest,
    )
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        store = ledger.memory_store()
        with pytest.raises(RealAssetError, match="model path"):
            build_real_memory_stack(
                connection=store._connection,
                memory_store=store,
                assets=assets,
                llama_config=config,
                world_mind_config=world_mind_config(),
                character_id="baiweixi",
            )
    finally:
        ledger.close()


def test_real_stack_factory_rejects_v6_reply_identity_drift(tmp_path) -> None:
    bundle_path, _, _, reply_root = make_bundle(
        tmp_path / "assets",
        world_id="other_world",
    )
    assets = LocalRealAssetBundle.load(bundle_path)
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"server")
    config = LlamaCppConfig(
        server_executable=server.resolve(),
        model_path=(reply_root / "model.gguf").resolve(),
        manifest_path=assets.llama_runtime_manifest,
    )
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        store = ledger.memory_store()
        with pytest.raises(RealAssetError, match="world_id"):
            build_real_memory_stack(
                connection=store._connection,
                memory_store=store,
                assets=assets,
                llama_config=config,
                world_mind_config=world_mind_config(),
                character_id="baiweixi",
            )
    finally:
        ledger.close()
