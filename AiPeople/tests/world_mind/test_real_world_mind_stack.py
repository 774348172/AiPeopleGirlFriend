from __future__ import annotations

from runtime._real_assets import LocalRealAssetBundle
from runtime.adapters.llama_cpp import LlamaCppConfig
from runtime.world_mind import WorldMindStore
from runtime.world_mind.real_stack import build_real_world_mind_stack

from tests.real_select_e2e._assets import make_bundle
from tests.world_mind._helpers import runtime_config, session


class Tokenizer:
    def encode(self, text, **kwargs):
        return list(range(max(1, len(text))))


class BgeBackend:
    tokenizer = Tokenizer()

    def encode(self, texts, **kwargs):
        return [[float(index + 1) for index in range(512)] for _ in texts]


class RerankerBackend:
    def score_pairs(self, pairs, *, maximum_tokens):
        return [0.9 for _ in pairs]

    def close(self):
        return None


def test_real_world_mind_stack_binds_assets_model_and_memory_repository(tmp_path) -> None:
    bundle_path, _, _, reply_root = make_bundle(tmp_path / "assets")
    assets = LocalRealAssetBundle.load(bundle_path)
    server = tmp_path / "llama-server.exe"
    server.write_bytes(b"server")
    llama = LlamaCppConfig(
        server_executable=server.resolve(),
        model_path=(reply_root / "model.gguf").resolve(),
        manifest_path=assets.llama_runtime_manifest,
    )
    store = WorldMindStore.open(tmp_path / "world-mind.sqlite3")
    try:
        stack = build_real_world_mind_stack(
            store=store,
            assets=assets,
            llama_config=llama,
            world_mind_config=runtime_config(),
            character_id="baiweixi",
            bge_loader=lambda _: BgeBackend(),
            reranker_loader=lambda _: RerankerBackend(),
        )
        repository = stack.memory_repository_factory.open(session())
        assert stack.world_mind_model.identity.character_id == "baiweixi"
        assert stack.world_mind_model.identity.world_id == "songjiangfu"
        assert stack.memory_proposer.identity == stack.world_mind_model.identity
        assert repository.owner_character_id == "baiweixi"
        assert repository.save_id == "save_001"
        assert stack.llama_backend.context_size == llama.context_size
    finally:
        store.close()
