from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime._memory_contracts import (
    EvidenceQuote,
    MemoryEpistemic,
    MemoryProposalDraft,
    MemoryRelations,
    MemorySubject,
    MemoryTemporal,
)
from runtime._real_assets import LocalRetrievalAssetBundle
from runtime.contracts import Completed
from runtime.world_mind import (
    ActiveSceneState,
    FakeWorldMindModel,
    GameClockService,
    InMemoryWorldStateProvider,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.real_stack import build_real_retrieval_stack
from runtime.world_mind.wmr08 import nearest_rank_summary, write_json


INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)


class FrozenProposalSet:
    async def propose(self, request):
        source = next(event for event in request.source_events if event.actor == "protagonist")
        facts = (
            ("player_fact", "男主喜欢雨夜", "我喜欢雨夜", "protagonist.weather.rain"),
            ("preference_boundary", "男主不喜欢香菜", "不喜欢香菜", "protagonist.food.coriander"),
            ("player_fact", "男主小时候住在四川", "小时候住在四川", "protagonist.origin.sichuan"),
            ("unfinished_topic", "男主想周末修咖啡机", "周末修咖啡机", "protagonist.plan.coffee_machine"),
        )
        return tuple(
            MemoryProposalDraft(
                kind=kind,
                statement=statement,
                subject=MemorySubject(
                    subject_type=("player" if kind != "unfinished_topic" else "player"),
                    entity_id=None,
                    display_name=None,
                ),
                epistemic=MemoryEpistemic(
                    polarity="affirmed",
                    modality="asserted",
                    grounding="speaker_report",
                ),
                temporal=MemoryTemporal(
                    relation="atemporal",
                    resolution="not_applicable",
                    source_text=None,
                    anchor_event_id=source.event_id,
                    start_at=None,
                    end_at=None,
                    timezone=None,
                    precision="not_applicable",
                ),
                evidence_quotes=(
                    EvidenceQuote(
                        event_id=source.event_id,
                        role="support",
                        quote=quote,
                        start_hint=source.text.index(quote),
                    ),
                ),
                semantic_reason="冻结检索工程集中的稳定长期信息",
                confidence=0.95,
                relation_suggestions=MemoryRelations(
                    semantic_slot=slot,
                    supersedes=(),
                    contradicts=(),
                    refines=(),
                ),
            )
            for kind, statement, quote, slot in facts
        )


def _config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
    )


def _session(save_id: str = "sys09_save", character_id: str = "baiweixi"):
    return RuntimeSessionIdentity(
        save_id=save_id,
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id=character_id,
        conversation_id=f"{save_id}_conversation",
    )


async def run_acceptance(bundle_path: Path, output_path: Path, samples: int) -> int:
    assets = LocalRetrievalAssetBundle.load(bundle_path)
    with tempfile.TemporaryDirectory(prefix="aipeople-sys09-") as directory:
        store = WorldMindStore.open(Path(directory) / "world_mind.sqlite3")
        clock = GameClockService(store, initial_game_time=INITIAL_GAME_TIME)
        provider = InMemoryWorldStateProvider()
        retrieval = build_real_retrieval_stack(store=store, assets=assets)
        runtime = WorldMindRuntime(
            config=_config(),
            store=store,
            world_state_provider=provider,
            game_clock=clock,
            model=FakeWorldMindModel(),
        )
        session = _session()
        started = time.perf_counter()
        try:
            await runtime.start()
            await provider.update_latest(
                session,
                ProtagonistLiveState(
                    protagonist_id="protagonist",
                    location_id="apartment_table",
                    location_label="出租屋餐桌旁",
                    activity="吃面",
                    body_state={"fatigue": "轻微疲惫"},
                    held_item_ids=("chopsticks",),
                ),
                ActiveSceneState(
                    scene_id="apartment_table",
                    location_label="出租屋餐桌旁",
                    present_character_ids=("protagonist", "baiweixi"),
                    item_states={"noodle_bowl": "在男主面前"},
                ),
                clock.current_time(session),
            )
            turn = await runtime.handle_turn(
                TurnRequest(
                    request_id="sys09-memory-seed",
                    session=session,
                    text="我喜欢雨夜，不喜欢香菜，小时候住在四川，周末修咖啡机。",
                )
            )
            if not isinstance(turn, Completed):
                raise RuntimeError(f"seed turn failed: {turn.code}")
            retrieval_started = time.perf_counter()
            await retrieval.memory_repository_factory.start()
            startup_ms = (time.perf_counter() - retrieval_started) * 1000
            repository = retrieval.memory_repository_factory.open(session)
            proposals = await repository.propose_from_request(
                "sys09-memory-seed", FrozenProposalSet()
            )
            memories = tuple(
                repository.commit(repository.materialize(proposal))
                for proposal in proposals
            )
            index_started = time.perf_counter()
            index = repository.rebuild()
            index_ms = (time.perf_counter() - index_started) * 1000
            cases = (
                ("下雨的时候你记得我是什么感受吗？", "男主喜欢雨夜"),
                ("我的饮食忌口是什么？", "男主不喜欢香菜"),
                ("我小时候住在哪里？", "男主小时候住在四川"),
                ("周末我准备处理什么东西？", "男主想周末修咖啡机"),
            )
            case_results = []
            latencies = []
            for ordinal in range(samples):
                query, expected = cases[ordinal % len(cases)]
                recall_started = time.perf_counter()
                result = await repository.recall(
                    query,
                    game_time=clock.current_time(session),
                )
                latency = (time.perf_counter() - recall_started) * 1000
                latencies.append(latency)
                selected = [item.statement for item in result.frame.selected_memories]
                case_results.append(
                    {
                        "query": query,
                        "expected_statement": expected,
                        "selected_statements": selected,
                        "passed": expected in selected,
                        "latency_ms": latency,
                    }
                )
            cross_save_repository = retrieval.memory_repository_factory.open(
                _session(save_id="sys09_other_save")
            )
            cross_character_repository = retrieval.memory_repository_factory.open(
                _session(character_id="another_heroine")
            )
            isolation_results = {
                "cross_save_visible_memory_ids": [
                    memory.memory_id for memory in cross_save_repository.list_memories()
                ],
                "cross_character_visible_memory_ids": [
                    memory.memory_id
                    for memory in cross_character_repository.list_memories()
                ],
                "cross_save_direct_read_hits": [
                    memory.memory_id
                    for memory in memories
                    if cross_save_repository.get_memory(memory.memory_id) is not None
                ],
                "cross_character_direct_read_hits": [
                    memory.memory_id
                    for memory in memories
                    if cross_character_repository.get_memory(memory.memory_id) is not None
                ],
            }
            isolation_passed = not any(isolation_results.values())
            report = {
                "schema_version": 1,
                "scope": "sys09_formal_retrieval_acceptance",
                "generated_at": datetime.now().astimezone().isoformat(),
                "assets": {
                    "bge": {
                        "revision": assets.bge.identity.revision,
                        "artifact_sha256": assets.bge.identity.artifact_sha256,
                    },
                    "reranker": {
                        "revision": assets.reranker.identity.revision,
                        "artifact_sha256": assets.reranker.identity.artifact_sha256,
                        "deployment_profile": assets.reranker.identity.deployment_profile,
                    },
                },
                "startup_ms": startup_ms,
                "index": {
                    "generation_id": index.generation_id,
                    "memory_count": index.memory_count,
                    "view_count": index.view_count,
                    "rebuild_ms": index_ms,
                },
                "memory_ids": [memory.memory_id for memory in memories],
                "isolation": {
                    **isolation_results,
                    "passed": isolation_passed,
                },
                "recall_ms": nearest_rank_summary(latencies),
                "cases": case_results,
                "decision": {
                    "correctness_passed": all(item["passed"] for item in case_results),
                    "p95_under_300ms": nearest_rank_summary(latencies)["p95"] < 300,
                    "isolation_passed": isolation_passed,
                    "sys09_gate": (
                        "passed"
                        if all(item["passed"] for item in case_results)
                        and nearest_rank_summary(latencies)["p95"] < 300
                        and isolation_passed
                        else "failed"
                    ),
                },
            }
            write_json(output_path, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["decision"]["sys09_gate"] == "passed" else 1
        finally:
            await retrieval.memory_repository_factory.close()
            await runtime.close()
            store.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SYS-09 formal retrieval acceptance.")
    parser.add_argument(
        "--bundle",
        default=str(
            ROOT / "local_runtime" / "models" / "retrieval" / "retrieval_assets.json"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "eval" / "world_mind_p0" / "sys09_retrieval_report.json"),
    )
    parser.add_argument("--samples", type=int, default=20)
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args()
    if args.samples < 4:
        raise SystemExit("--samples must be at least 4")
    raise SystemExit(
        asyncio.run(
            run_acceptance(Path(args.bundle), Path(args.output), args.samples)
        )
    )
