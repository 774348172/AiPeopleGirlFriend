from __future__ import annotations

import json

from runtime._real_assets import LocalRetrievalAssetBundle
from runtime.gemma_nf4_assets import GEMMA4_MODEL_ID, LocalGemmaNF4Package
from runtime.world_mind import WorldMindStore
from runtime.world_mind.gemma_nf4_stack import build_gemma_nf4_world_mind_stack
from tests.real_select_e2e._assets import make_bge_package, make_reranker_package
from tests.real_select_e2e.test_gemma_nf4_assets import make_asset
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


def _make_retrieval_bundle(tmp_path):
    bge_root = make_bge_package(tmp_path / "bge")
    reranker_root = make_reranker_package(tmp_path / "reranker")
    bge = json.loads((bge_root / "bge_manifest.json").read_text(encoding="utf-8"))
    reranker = json.loads(
        (reranker_root / "reranker_manifest.json").read_text(encoding="utf-8")
    )
    path = tmp_path / "retrieval_asset_bundle.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "network_access_at_runtime": "forbidden",
                "bge": {
                    "path": str(bge_root.resolve()),
                    "revision": bge["revision"],
                    "artifact_sha256": bge["artifact_sha256"],
                },
                "reranker": {
                    "path": str(reranker_root.resolve()),
                    "revision": reranker["revision"],
                    "artifact_sha256": reranker["artifact_sha256"],
                },
            }
        ),
        encoding="utf-8",
    )
    return LocalRetrievalAssetBundle.load(path)


def test_gemma_nf4_stack_binds_reply_identity_and_retrieval(tmp_path) -> None:
    retrieval = _make_retrieval_bundle(tmp_path / "retrieval")
    reply_root = tmp_path / "reply"
    reply_root.mkdir()
    reply = LocalGemmaNF4Package.load(make_asset(reply_root))
    store = WorldMindStore.open(tmp_path / "world-mind.sqlite3")
    try:
        stack = build_gemma_nf4_world_mind_stack(
            store=store,
            retrieval_assets=retrieval,
            reply_asset=reply,
            world_mind_config=runtime_config(),
            character_id="baiweixi",
            bge_loader=lambda _: BgeBackend(),
            reranker_loader=lambda _: RerankerBackend(),
            gemma_loader=lambda _: (object(), object()),
        )
        repository = stack.memory_repository_factory.open(session())
        assert stack.reply_asset is reply
        assert stack.retrieval_assets is retrieval
        assert stack.backend.context_size == 4096
        assert stack.backend.state == "stopped"
        assert stack.world_mind_model.identity.model_id == GEMMA4_MODEL_ID
        assert stack.world_mind_model.identity.revision == "adapter-revision-test"
        assert stack.world_mind_model.identity.character_id == "baiweixi"
        assert stack.world_mind_model.identity.world_id == "songjiangfu"
        assert stack.memory_proposer.identity == stack.world_mind_model.identity
        assert repository.owner_character_id == "baiweixi"
    finally:
        store.close()
