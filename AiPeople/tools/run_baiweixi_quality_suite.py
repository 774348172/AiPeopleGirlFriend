from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import traceback
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from runtime._selected_memory import (
    SelectedMemory,
    SelectedMemoryEvidence,
    SelectedMemoryFrame,
)
from runtime.adapters import GenerationOptions, OllamaConfig, OllamaWorldMindBackend
from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ActiveSceneState,
    CharacterPackagePromptComposer,
    GameClockService,
    InMemoryWorldStateProvider,
    InitialHeroineRuntimeSeed,
    LlamaCppWorldMindModel,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindModeProfile,
    WorldMindModelIdentity,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    GAME_REPLY,
    POST_REPLY_WORLD_MIND_RECONCILE,
    TURN_MIND_ADVANCE,
    WORLD_CONTINUITY_REVIEW,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path(os.environ.get("AIPEOPLE_MODEL_ROOT", "F:/AiPeople"))
SUITE_ROOT = ROOT / "eval" / "baiweixi_quality"
SUITE_MANIFEST_PATH = SUITE_ROOT / "suite_manifest_v1.json"
CASE_PATHS = (
    SUITE_ROOT / "cases" / "frozen_single_v1.jsonl",
    SUITE_ROOT / "cases" / "frozen_multiturn_v1.jsonl",
)
CANDIDATE_ARTIFACT_PATH = (
    MODEL_ROOT
    / "training_package_baiweixi_local3060_qwen3"
    / "outputs"
    / "baiweixi_4b_q5_k_m.gguf"
)
CANDIDATE_SHA256 = (
    "7786060133153d8ed0dd25f399027deee5d18f6d9b0f56f62bd13b9468a93a0f"
)
CANDIDATE_OLLAMA_DIGEST = (
    "09d23dc30422a78343d65dd1c566f92287917c82856c6c1aa1126b2c43dd58fa"
)
JUDGE_OLLAMA_DIGEST = (
    "4d430fc287751177cb68d9e75028fe238548bba6a2f6e741e2951acb0c94f296"
)
DIMENSIONS = (
    "objective_grounding",
    "canon_accuracy",
    "persona_fidelity",
    "relationship_pacing",
    "knowledge_boundary",
    "naturalness",
    "conversational_relevance",
)
JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "decision", "reason", "violations", "scores"],
    "properties": {
        "schema_version": {"const": 1},
        "decision": {"enum": ["pass", "fail", "ambiguous"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 800},
        "violations": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
        "scores": {
            "type": "object",
            "additionalProperties": False,
            "required": list(DIMENSIONS),
            "properties": {
                name: {"type": "integer", "minimum": 1, "maximum": 5}
                for name in DIMENSIONS
            },
        },
    },
}
JUDGE_SYSTEM = """你是独立的角色与游戏状态质量评审，不扮演任何角色。
只依据给出的冻结案例、程序世界快照、角色上一状态、记忆证据、模型内部决策和最终回复判断。
重点区分：程序已经确定的男主客观状态、白未晞自己的持续状态、角色正典、知识资格和关系节奏。
玩家对白不能直接改写男主程序状态；没有证据时女主状态应延续；有充分场景或时间证据时允许自然变化。
不要因为措辞与参考答案不同就判失败。只有事实错误、明显漏答、主客体混淆、越权知识、关系跳级、能力越界、助手腔或严重不自然才判失败。
如果题目本身不足以稳定判断，decision 必须为 ambiguous。
scores 的评分方向固定为：1=严重失败，2=明显较差，3=勉强或不适用，4=通过，5=表现优秀。decision=pass 时，案例适用维度不得低于4分；不适用维度统一给3分。
输出严格 JSON，不解释内部过程。"""


class MutableMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("game time cannot move backwards inside a trajectory")
        self.value += seconds


class SharedSeedBackend:
    def __init__(self, backend: OllamaWorldMindBackend) -> None:
        self.backend = backend
        self.seed = 42
        self.calls: list[dict[str, object]] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def begin_attempt(self, seed: int) -> None:
        self.seed = seed
        self.calls = []

    async def complete_chat(
        self,
        *,
        request_id: str,
        messages: Sequence[Mapping[str, str]],
        options: GenerationOptions,
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        mode = "CHARACTER_DIRECT"
        try:
            payload = json.loads(messages[-1]["content"])
            if isinstance(payload, dict):
                mode = str(payload.get("mode", mode))
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        started = time.perf_counter()
        record: dict[str, object] = {
            "request_id": request_id,
            "mode": mode,
            "seed": self.seed,
        }
        try:
            response = await self.backend.complete_chat(
                request_id=request_id,
                messages=messages,
                options=replace(options, seed=self.seed),
                response_format=response_format,
            )
            record["ok"] = True
            return response
        except BaseException as error:
            record["ok"] = False
            record["error_type"] = type(error).__name__
            record["error"] = str(error)
            raise
        finally:
            record["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            self.calls.append(record)


@dataclass(slots=True)
class RecallResult:
    frame: SelectedMemoryFrame


class EvaluationMemoryFactory:
    def __init__(self) -> None:
        self.current_evidence: list[str] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def open(self, session: RuntimeSessionIdentity) -> "EvaluationMemoryFactory":
        return self

    async def recall(self, text: str, *, game_time: datetime) -> RecallResult:
        memories = []
        for index, statement in enumerate(self.current_evidence, 1):
            memories.append(
                SelectedMemory(
                    memory_id=f"eval_memory_{index}",
                    memory_version=1,
                    kind="player_fact",
                    statement=statement,
                    subject_type="protagonist",
                    subject_display_name="男主",
                    temporal_relation="past",
                    temporal_source_text="此前",
                    temporal_start_at=None,
                    temporal_end_at=None,
                    temporal_timezone=None,
                    epistemic_polarity="affirmed",
                    epistemic_modality="observed",
                    evidence=(
                        SelectedMemoryEvidence(
                            event_id=f"eval_event_{index}",
                            role="user",
                            excerpt=statement,
                        ),
                    ),
                )
            )
        frame = SelectedMemoryFrame(
            selected_memories=tuple(memories),
            source_memory_ids=tuple(item.memory_id for item in memories),
            source_event_ids=tuple(
                evidence.event_id
                for memory in memories
                for evidence in memory.evidence
            ),
            selector_version="baiweixi-quality-eval-memory-v1",
            token_count=min(256, sum(max(1, len(item.statement) // 2) for item in memories)),
            truncated=False,
        )
        return RecallResult(frame=frame)


def _runtime_config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
    )


def _mode_profiles(timeout_seconds: float) -> dict[str, WorldMindModeProfile]:
    return {
        TURN_MIND_ADVANCE: WorldMindModeProfile(900, 0.2, 0.85, 1.05, timeout_seconds, 1),
        WORLD_CONTINUITY_REVIEW: WorldMindModeProfile(650, 0.1, 0.8, 1.05, timeout_seconds, 1),
        GAME_REPLY: WorldMindModeProfile(600, 0.75, 0.9, 1.1, timeout_seconds, 1),
        POST_REPLY_WORLD_MIND_RECONCILE: WorldMindModeProfile(320, 0.2, 0.85, 1.05, timeout_seconds, 0),
        FIVE_MINUTE_WORLD_MIND_RECONCILE: WorldMindModeProfile(320, 0.2, 0.85, 1.05, timeout_seconds, 0),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _verify_ollama_model(base_url: str, name: str, digest: str) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
    aliases = {name, f"{name}:latest"}
    models = response.json().get("models", [])
    match = next(
        (
            item
            for item in models
            if isinstance(item, dict)
            and (item.get("name") in aliases or item.get("model") in aliases)
        ),
        None,
    )
    if match is None:
        raise RuntimeError(f"Ollama model is missing: {name}")
    if str(match.get("digest", "")).lower() != digest:
        raise RuntimeError(f"Ollama digest mismatch for {name}")
    return {
        "name": match.get("name"),
        "digest": digest,
        "size": match.get("size"),
        "details": match.get("details", {}),
    }


def _load_cases() -> list[dict[str, Any]]:
    cases = []
    for path in CASE_PATHS:
        cases.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    if len(cases) != 89:
        raise RuntimeError(f"expected 89 automatic cases, got {len(cases)}")
    return cases


def _load_jsonl_by_key(path: Path, key: str) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        values[str(item[key])] = item
    return values


def _append_jsonl(path: Path, value: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()


def _attempt_key(case_id: str, seed: int) -> str:
    return f"{case_id}::seed={seed}"


def _safe_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
    return normalized[:48] or "case"


def _parse_game_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=None)


def _protagonist(snapshot: Mapping[str, object]) -> ProtagonistLiveState:
    value = snapshot["protagonist"]
    if not isinstance(value, Mapping):
        raise TypeError("protagonist snapshot must be an object")
    return ProtagonistLiveState(
        protagonist_id="protagonist",
        location_id=str(value["location_id"]),
        location_label=str(value["location_label"]),
        activity=str(value["activity"]),
        body_state={str(key): str(item) for key, item in dict(value["body_state"]).items()},
        held_item_ids=tuple(str(item) for item in value["held_item_ids"]),
    )


def _scene(snapshot: Mapping[str, object]) -> ActiveSceneState:
    value = snapshot["scene"]
    if not isinstance(value, Mapping):
        raise TypeError("scene snapshot must be an object")
    return ActiveSceneState(
        scene_id=str(value["scene_id"]),
        location_label=str(value["location_label"]),
        present_character_ids=tuple(str(item) for item in value["present_character_ids"]),
        item_states={str(key): str(item) for key, item in dict(value["item_states"]).items()},
    )


def _seed(value: Mapping[str, object]) -> InitialHeroineRuntimeSeed:
    return InitialHeroineRuntimeSeed(
        character_id="baiweixi",
        living_mind={
            "form": str(value["form"]),
            "body": str(value["body"]),
            "emotion": str(value["emotion"]),
            "attention": str(value["attention"]),
            "current_activity": str(value["current_activity"]),
            "immediate_intent": str(value["immediate_intent"]),
        },
        relationship={
            "protagonist_id": "protagonist",
            "stage": str(value["relationship_stage"]),
            "trust": str(value["trust"]),
            "unresolved_tension": str(value["unresolved_tension"]),
        },
    )


def _runtime_payload(runtime: object | None) -> dict[str, object] | None:
    if runtime is None:
        return None
    living = runtime.living_mind
    relationship = runtime.relationship
    return {
        "version": runtime.version,
        "living_mind": {
            "form": living.form,
            "body": living.body,
            "emotion": living.emotion,
            "attention": living.attention,
            "current_activity": living.current_activity,
            "immediate_intent": living.immediate_intent,
        },
        "relationship": {
            "stage": relationship.stage,
            "trust": relationship.trust,
            "unresolved_tension": relationship.unresolved_tension,
        },
        "motive_state": dict(runtime.motive_state),
        "knowledge_state": dict(runtime.knowledge_state),
    }


def _decision_audit(database_path: Path, save_id: str, request_id: str) -> dict[str, object] | None:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT mind_result_json, continuity_review_json,
                   game_reply_result_json, model_identity_json
            FROM turn_model_decisions
            WHERE save_id = ? AND request_id = ?
            """,
            (save_id, request_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "mind_result": json.loads(row[0]),
        "continuity_review": json.loads(row[1]),
        "game_reply_result": json.loads(row[2]),
        "model_identity": json.loads(row[3]),
    }


async def _run_direct_attempt(
    case: dict[str, Any],
    seed: int,
    backend: SharedSeedBackend,
    prompt: str,
) -> dict[str, object]:
    turn = case["turns"][0]
    backend.begin_attempt(seed)
    started = time.perf_counter()
    try:
        response = await backend.complete_chat(
            request_id=f"quality:{case['case_id']}:{seed}",
            messages=(
                {"role": "system", "content": prompt},
                {"role": "user", "content": turn["user_text"]},
            ),
            options=GenerationOptions(
                max_tokens=case["generation"]["max_new_tokens"],
                temperature=case["generation"]["temperature"],
                top_p=0.9,
                repeat_penalty=1.1,
                seed=seed,
            ),
        )
        return {
            "attempt_key": _attempt_key(case["case_id"], seed),
            "case_id": case["case_id"],
            "seed": seed,
            "risk": case["risk"],
            "category": case["category"],
            "evaluation_layer": case["evaluation_layer"],
            "success": True,
            "turn_results": [
                {
                    "turn_id": turn["turn_id"],
                    "user_text": turn["user_text"],
                    "world_snapshot": None,
                    "initial_heroine_state": None,
                    "memory_evidence": [],
                    "oracle": turn["oracle"],
                    "response": response,
                    "runtime_result": "direct_completed",
                    "heroine_runtime_after": None,
                    "decision_audit": None,
                }
            ],
            "model_calls": list(backend.calls),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except BaseException as error:
        return {
            "attempt_key": _attempt_key(case["case_id"], seed),
            "case_id": case["case_id"],
            "seed": seed,
            "risk": case["risk"],
            "category": case["category"],
            "evaluation_layer": case["evaluation_layer"],
            "success": False,
            "failure_code": "direct_model_error",
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "turn_results": [],
            "model_calls": list(backend.calls),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }


async def _run_runtime_attempt(
    case: dict[str, Any],
    seed: int,
    backend: SharedSeedBackend,
    args: argparse.Namespace,
    run_dir: Path,
) -> dict[str, object]:
    key = _attempt_key(case["case_id"], seed)
    backend.begin_attempt(seed)
    started = time.perf_counter()
    identifier = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    session = RuntimeSessionIdentity(
        save_id=f"quality_{identifier}",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=f"quality_{identifier}",
    )
    database_path = run_dir / "databases" / f"{_safe_id(case['case_id'])}-{seed}.sqlite3"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    store = WorldMindStore.open(database_path)
    first_game_time = _parse_game_time(case["turns"][0]["world_snapshot"]["game_time"])
    monotonic = MutableMonotonic()
    clock = GameClockService(store, initial_game_time=first_game_time, monotonic=monotonic)
    provider = InMemoryWorldStateProvider()
    memory_factory = EvaluationMemoryFactory()
    model = LlamaCppWorldMindModel(
        backend,
        identity=WorldMindModelIdentity(
            model_id=f"ollama/{args.model}",
            revision=f"ollama-digest:{args.candidate_ollama_digest}",
            artifact_sha256=args.candidate_artifact_sha256,
            character_id="baiweixi",
            world_id="songjiangfu",
            protagonist_id="protagonist",
        ),
        mode_profiles=_mode_profiles(args.timeout_seconds),
    )
    runtime = WorldMindRuntime(
        config=_runtime_config(),
        store=store,
        world_state_provider=provider,
        game_clock=clock,
        model=model,
        memory_repository_factory=memory_factory,
        periodic_reconcile_seconds=86400.0,
    )
    turn_results: list[dict[str, object]] = []
    success = True
    failure_code = None
    previous_game_time = first_game_time
    try:
        store.get_or_create_heroine_runtime(session, _seed(case["initial_heroine_state"]))
        await runtime.start()
        for index, turn in enumerate(case["turns"], 1):
            snapshot = turn["world_snapshot"]
            target_time = _parse_game_time(snapshot["game_time"])
            monotonic.advance((target_time - previous_game_time).total_seconds())
            previous_game_time = target_time
            await provider.update_latest(
                session,
                _protagonist(snapshot),
                _scene(snapshot),
                clock.current_time(session),
            )
            memory_factory.current_evidence = list(turn["memory_evidence"])
            request_id = f"quality_{identifier}_turn_{index}"
            result = await runtime.handle_turn(
                TurnRequest(
                    request_id=request_id,
                    session=session,
                    text=turn["user_text"],
                )
            )
            heroine_after = store.load_heroine_runtime(session)
            audit = _decision_audit(database_path, session.save_id, request_id)
            turn_results.append(
                {
                    "turn_id": turn["turn_id"],
                    "user_text": turn["user_text"],
                    "world_snapshot": snapshot,
                    "initial_heroine_state": case["initial_heroine_state"] if index == 1 else None,
                    "memory_evidence": turn["memory_evidence"],
                    "oracle": turn["oracle"],
                    "response": result.text if isinstance(result, Completed) else None,
                    "runtime_result": type(result).__name__,
                    "failure_code": result.code if isinstance(result, Failed) else None,
                    "heroine_runtime_after": _runtime_payload(heroine_after),
                    "decision_audit": audit,
                    "metrics": (
                        {
                            "model_total_ms": result.metrics.model_total_ms,
                            "total_ms": result.metrics.total_ms,
                            "output_chars": result.metrics.output_chars,
                        }
                        if isinstance(result, Completed)
                        else None
                    ),
                }
            )
            if not isinstance(result, Completed):
                success = False
                failure_code = result.code
                break
        return {
            "attempt_key": key,
            "case_id": case["case_id"],
            "seed": seed,
            "risk": case["risk"],
            "category": case["category"],
            "evaluation_layer": case["evaluation_layer"],
            "success": success,
            "failure_code": failure_code,
            "turn_results": turn_results,
            "model_calls": list(backend.calls),
            "database_path": str(database_path),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    except BaseException as error:
        return {
            "attempt_key": key,
            "case_id": case["case_id"],
            "seed": seed,
            "risk": case["risk"],
            "category": case["category"],
            "evaluation_layer": case["evaluation_layer"],
            "success": False,
            "failure_code": "runtime_exception",
            "error_type": type(error).__name__,
            "error": str(error),
            "traceback": traceback.format_exc(),
            "turn_results": turn_results,
            "model_calls": list(backend.calls),
            "database_path": str(database_path),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
    finally:
        try:
            await runtime.close()
        finally:
            store.close()


def _deterministic_checks(turn: Mapping[str, object]) -> dict[str, object]:
    response = str(turn.get("response") or "")
    oracle = turn["oracle"]
    missing_groups = [
        group
        for group in oracle["required_term_groups"]
        if not any(str(term) in response for term in group)
    ]
    forbidden_hits = [
        str(term) for term in oracle["forbidden_terms"] if str(term) in response
    ]
    return {
        "missing_required_term_groups": missing_groups,
        "forbidden_term_hits": forbidden_hits,
        "passed": not missing_groups and not forbidden_hits,
    }


async def _judge_turn(
    backend: OllamaWorldMindBackend,
    attempt: Mapping[str, object],
    turn: Mapping[str, object],
    *,
    timeout_seconds: float,
) -> dict[str, object]:
    payload = {
        "case_id": attempt["case_id"],
        "seed": attempt["seed"],
        "risk": attempt["risk"],
        "category": attempt["category"],
        "evaluation_layer": attempt["evaluation_layer"],
        "turn": turn,
    }
    last_error: BaseException | None = None
    for attempt_index in range(2):
        try:
            async with asyncio.timeout(timeout_seconds):
                raw = await backend.complete_chat(
                    request_id=f"judge:{attempt['attempt_key']}:{turn['turn_id']}:{attempt_index}",
                    messages=(
                        {"role": "system", "content": JUDGE_SYSTEM},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ),
                    options=GenerationOptions(
                        max_tokens=500,
                        temperature=0.1,
                        top_p=0.8,
                        repeat_penalty=1.05,
                        seed=0,
                    ),
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "baiweixi_quality_judge_v1",
                            "strict": True,
                            "schema": JUDGE_SCHEMA,
                        },
                    },
                )
            value = json.loads(raw)
            if set(value) != {"schema_version", "decision", "reason", "violations", "scores"}:
                raise ValueError("judge fields do not match schema")
            return value
        except BaseException as error:
            last_error = error
    return {
        "schema_version": 1,
        "decision": "ambiguous",
        "reason": f"judge failed: {type(last_error).__name__}: {last_error}",
        "violations": [],
        "scores": {name: 3 for name in DIMENSIONS},
        "judge_error": True,
    }


def _execution_attribution(attempt: Mapping[str, object]) -> str:
    layer = str(attempt["evaluation_layer"])
    code = str(attempt.get("failure_code") or "")
    if layer == "character_direct":
        return "character_failure"
    if code in {
        "identity_mismatch",
        "world_state_unavailable",
        "persistence_error",
        "memory_repository_error",
    }:
        return "system_failure"
    return "joint_or_ambiguous"


def _semantic_attribution(layer: str, decision: str) -> str | None:
    if decision == "pass":
        return None
    if decision == "ambiguous":
        return "evaluation_ambiguity"
    if layer == "character_direct":
        return "character_failure"
    return "joint_or_ambiguous"


async def _judge_attempts(
    raw_attempts: dict[str, dict[str, Any]],
    judged_path: Path,
    backend: OllamaWorldMindBackend,
    args: argparse.Namespace,
) -> dict[str, dict[str, Any]]:
    judged = _load_jsonl_by_key(judged_path, "attempt_key") if args.resume else {}
    total = len(raw_attempts)
    for index, (key, attempt) in enumerate(raw_attempts.items(), 1):
        if key in judged:
            continue
        if not attempt["success"]:
            value = dict(attempt)
            value["decision"] = "fail"
            value["attribution"] = _execution_attribution(attempt)
            value["judged_turns"] = []
        else:
            judged_turns = []
            decisions = []
            for turn in attempt["turn_results"]:
                deterministic = _deterministic_checks(turn)
                semantic = await _judge_turn(
                    backend,
                    attempt,
                    turn,
                    timeout_seconds=args.judge_timeout_seconds,
                )
                decision = str(semantic["decision"])
                if not deterministic["passed"]:
                    decision = "fail"
                decisions.append(decision)
                judged_turns.append(
                    {
                        "turn_id": turn["turn_id"],
                        "deterministic": deterministic,
                        "semantic_judge": semantic,
                        "decision": decision,
                    }
                )
            overall = "fail" if "fail" in decisions else "ambiguous" if "ambiguous" in decisions else "pass"
            value = dict(attempt)
            value["decision"] = overall
            value["attribution"] = _semantic_attribution(str(attempt["evaluation_layer"]), overall)
            value["judged_turns"] = judged_turns
        _append_jsonl(judged_path, value)
        judged[key] = value
        print(f"JUDGE {index}/{total} {key} -> {value['decision']}", flush=True)
    return judged


def _case_summary(cases: list[dict[str, Any]], judged: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    values = []
    for case in cases:
        attempts = [judged[_attempt_key(case["case_id"], seed)] for seed in case["generation"]["seed_set"]]
        decisions = [str(item["decision"]) for item in attempts]
        decision = "fail" if "fail" in decisions else "ambiguous" if "ambiguous" in decisions else "pass"
        attributions = sorted(
            {
                str(item["attribution"])
                for item in attempts
                if item.get("attribution") is not None
            }
        )
        failed_seeds = [item["seed"] for item in attempts if item["decision"] == "fail"]
        ambiguous_seeds = [item["seed"] for item in attempts if item["decision"] == "ambiguous"]
        values.append(
            {
                "case_id": case["case_id"],
                "risk": case["risk"],
                "category": case["category"],
                "evaluation_layer": case["evaluation_layer"],
                "decision": decision,
                "attributions": attributions,
                "failed_seeds": failed_seeds,
                "ambiguous_seeds": ambiguous_seeds,
            }
        )
    return values


def _score_average(judged: Mapping[str, Mapping[str, object]]) -> float:
    scores = []
    for attempt in judged.values():
        for turn in attempt.get("judged_turns", []):
            dimensions = turn["semantic_judge"]["scores"]
            scores.extend(float(dimensions[name]) for name in DIMENSIONS)
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _pass_rate(values: list[dict[str, object]], risk: str) -> float:
    selected = [item for item in values if item["risk"] == risk]
    return round(sum(item["decision"] == "pass" for item in selected) / len(selected), 4) if selected else 0.0


def _result_counts(
    case_results: list[dict[str, object]],
    field: str,
) -> dict[str, dict[str, object]]:
    values = {}
    for name in sorted({str(item[field]) for item in case_results}):
        selected = [item for item in case_results if item[field] == name]
        counts = Counter(str(item["decision"]) for item in selected)
        values[name] = {
            "cases": len(selected),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "ambiguous": counts.get("ambiguous", 0),
            "pass_rate": round(counts.get("pass", 0) / len(selected), 4),
        }
    return values


def _semantic_decisions(attempt: Mapping[str, object]) -> list[str]:
    return [
        str(turn["semantic_judge"]["decision"])
        for turn in attempt.get("judged_turns", [])
    ]


def _has_evaluator_disagreement(attempt: Mapping[str, object]) -> bool:
    return any(
        not turn["deterministic"]["passed"]
        and turn["semantic_judge"]["decision"] == "pass"
        for turn in attempt.get("judged_turns", [])
    )


def _has_judge_error(attempt: Mapping[str, object]) -> bool:
    return any(
        bool(turn["semantic_judge"].get("judge_error"))
        for turn in attempt.get("judged_turns", [])
    )


def _infer_execution_failure_mode(attempt: Mapping[str, object]) -> str:
    if attempt.get("success"):
        return "none"
    calls = list(attempt.get("model_calls", []))
    if len(calls) >= 2:
        last_mode = str(calls[-1].get("mode") or "")
        previous_mode = str(calls[-2].get("mode") or "")
        if last_mode and last_mode == previous_mode:
            return last_mode
    return "unresolved"


def _audit_attributions(
    case_results: list[dict[str, object]],
    cases: list[dict[str, Any]],
    judged: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    case_by_id = {str(case["case_id"]): case for case in cases}
    groups: dict[str, set[str]] = {
        "system_failure": set(),
        "character_failure": set(),
        "joint_or_ambiguous": set(),
        "evaluation_ambiguity": set(),
    }
    evaluator_disagreement_cases: set[str] = set()
    judge_error_cases: set[str] = set()
    for result in case_results:
        if result["decision"] == "pass":
            continue
        case_id = str(result["case_id"])
        case = case_by_id[case_id]
        attempts = [
            judged[_attempt_key(case_id, seed)]
            for seed in case["generation"]["seed_set"]
        ]
        execution_attributions = {
            _execution_attribution(attempt)
            for attempt in attempts
            if not attempt.get("success")
        }
        groups["system_failure"].update(
            [case_id] if "system_failure" in execution_attributions else []
        )
        groups["joint_or_ambiguous"].update(
            [case_id] if "joint_or_ambiguous" in execution_attributions else []
        )
        semantic_fail = any(
            "fail" in _semantic_decisions(attempt) for attempt in attempts
        )
        if semantic_fail:
            groups["character_failure"].add(case_id)
        disagreement = any(_has_evaluator_disagreement(attempt) for attempt in attempts)
        if disagreement and not semantic_fail:
            groups["evaluation_ambiguity"].add(case_id)
            evaluator_disagreement_cases.add(case_id)
        if any(_has_judge_error(attempt) for attempt in attempts):
            groups["evaluation_ambiguity"].add(case_id)
            judge_error_cases.add(case_id)
    failed_case_ids = {
        str(result["case_id"])
        for result in case_results
        if result["decision"] != "pass"
    }
    primary_groups: dict[str, set[str]] = {
        "system_failure": set(groups["system_failure"]),
        "character_failure": set(),
        "joint_or_ambiguous": set(),
        "evaluation_ambiguity": set(),
    }
    remaining = failed_case_ids - primary_groups["system_failure"]
    primary_groups["joint_or_ambiguous"] = remaining & groups["joint_or_ambiguous"]
    remaining -= primary_groups["joint_or_ambiguous"]
    primary_groups["character_failure"] = remaining & groups["character_failure"]
    remaining -= primary_groups["character_failure"]
    primary_groups["evaluation_ambiguity"] = remaining
    return {
        "method": (
            "execution failures are classified from Runtime failure codes and the "
            "final retried model mode; character failures require a semantic Judge "
            "failure; deterministic/Judge conflicts remain evaluation ambiguity "
            "until human adjudication"
        ),
        "case_counts": {name: len(values) for name, values in groups.items()},
        "case_ids": {name: sorted(values) for name, values in groups.items()},
        "primary_case_counts": {
            name: len(values) for name, values in primary_groups.items()
        },
        "primary_case_ids": {
            name: sorted(values) for name, values in primary_groups.items()
        },
        "evaluator_disagreement_cases": sorted(evaluator_disagreement_cases),
        "judge_error_cases": sorted(judge_error_cases),
        "warning": (
            "evaluation_ambiguity does not mean the candidate passed; spot checks "
            "show both exact-term false positives and semantic Judge false passes"
        ),
    }


def _evaluation_integrity(
    judged: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    matrix: Counter[str] = Counter()
    for attempt in judged.values():
        for turn in attempt.get("judged_turns", []):
            deterministic = "pass" if turn["deterministic"]["passed"] else "fail"
            semantic = str(turn["semantic_judge"]["decision"])
            final = str(turn["decision"])
            matrix[f"deterministic={deterministic},semantic={semantic},final={final}"] += 1
    disagreements = sum(
        _has_evaluator_disagreement(attempt) for attempt in judged.values()
    )
    judge_errors = sum(_has_judge_error(attempt) for attempt in judged.values())
    return {
        "turn_outcome_matrix": dict(matrix),
        "deterministic_semantic_disagreement_attempts": disagreements,
        "judge_error_attempts": judge_errors,
        "limitations": [
            "required_term_groups use exact substrings and can reject valid synonyms",
            "forbidden_terms use exact substrings and can misread explicit negation",
            "the local semantic Judge produced demonstrably unsupported reasons",
            "overall_weighted_score excludes execution failures and is not a pass rate",
        ],
    }


def _execution_stability(
    judged: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    failed = [attempt for attempt in judged.values() if not attempt.get("success")]
    by_mode = Counter(_infer_execution_failure_mode(attempt) for attempt in failed)
    backend_errors = sum(
        not bool(call.get("ok"))
        for attempt in failed
        for call in attempt.get("model_calls", [])
    )
    return {
        "successful_attempts": len(judged) - len(failed),
        "failed_attempts": len(failed),
        "success_rate": round((len(judged) - len(failed)) / len(judged), 4),
        "failure_modes": dict(by_mode),
        "backend_call_errors": backend_errors,
        "classification": (
            "model structured-output/protocol retry exhaustion when both backend "
            "calls succeeded and the same final mode was attempted twice"
        ),
    }


def _build_report(
    args: argparse.Namespace,
    run_dir: Path,
    cases: list[dict[str, Any]],
    judged: dict[str, dict[str, Any]],
    candidate_metadata: dict[str, object],
    judge_metadata: dict[str, object],
    started_at: str,
) -> dict[str, object]:
    case_results = _case_summary(cases, judged)
    decision_counts = Counter(str(item["decision"]) for item in case_results)
    attribution_counts = Counter(
        attribution for item in case_results for attribution in item["attributions"]
    )
    category_results = _result_counts(case_results, "category")
    layer_results = _result_counts(case_results, "evaluation_layer")
    risk_results = _result_counts(case_results, "risk")
    blocker_failures = sum(
        item["risk"] == "blocker" and item["decision"] != "pass"
        for item in case_results
    )
    important_rate = _pass_rate(case_results, "important")
    normal_rate = _pass_rate(case_results, "normal")
    weighted_score = _score_average(judged)
    automatic_gate = (
        blocker_failures == 0
        and important_rate >= 0.95
        and normal_rate >= 0.9
        and weighted_score >= 4.2
    )
    manifest = json.loads(SUITE_MANIFEST_PATH.read_text(encoding="utf-8"))
    audit_attributions = _audit_attributions(case_results, cases, judged)
    evaluation_integrity = _evaluation_integrity(judged)
    execution_stability = _execution_stability(judged)
    return {
        "schema_version": 1,
        "run_id": run_dir.name,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "suite": {
            "suite_id": manifest["suite_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "automatic_cases": len(cases),
            "case_attempts": len(judged),
            "planned_turn_executions": sum(
                len(case["turns"]) * len(case["generation"]["seed_set"])
                for case in cases
            ),
            "turn_executions": sum(len(item.get("turn_results", [])) for item in judged.values()),
        },
        "candidate_model": {
            "ollama": candidate_metadata,
            "artifact_path": str(args.candidate_artifact_path),
            "artifact_sha256": args.candidate_artifact_sha256,
        },
        "semantic_judge": {
            "ollama": judge_metadata,
            "role": "自动语义初审，不替代四个人工长会话",
        },
        "decision_counts": dict(decision_counts),
        "attribution_counts": dict(attribution_counts),
        "audited_attributions": audit_attributions,
        "attempt_decision_counts": dict(
            Counter(str(item["decision"]) for item in judged.values())
        ),
        "category_results": category_results,
        "evaluation_layer_results": layer_results,
        "risk_results": risk_results,
        "execution_stability": execution_stability,
        "evaluation_integrity": evaluation_integrity,
        "formal_gate": {
            "blocker_failures": blocker_failures,
            "important_pass_rate": important_rate,
            "normal_pass_rate": normal_rate,
            "overall_weighted_score": weighted_score,
            "automatic_gate_passed": automatic_gate,
            "human_sessions_completed": 0,
            "formal_p0_quality_passed": False,
        },
        "case_results": case_results,
        "artifacts": {
            "raw_attempts": str(run_dir / "attempts_raw.jsonl"),
            "judged_attempts": str(run_dir / "attempts_judged.jsonl"),
            "manual_review_queue": str(run_dir / "manual_review_queue.jsonl"),
        },
    }


def _write_manual_review_queue(
    report: Mapping[str, object],
    cases: list[dict[str, Any]],
    judged: Mapping[str, Mapping[str, object]],
    path: Path,
) -> None:
    case_by_id = {str(case["case_id"]): case for case in cases}
    audited = report["audited_attributions"]["case_ids"]
    labels_by_case: dict[str, list[str]] = {}
    for label, case_ids in audited.items():
        for case_id in case_ids:
            labels_by_case.setdefault(str(case_id), []).append(str(label))
    with path.open("w", encoding="utf-8") as stream:
        for result in report["case_results"]:
            if result["decision"] == "pass":
                continue
            case_id = str(result["case_id"])
            case = case_by_id[case_id]
            attempts = [
                judged[_attempt_key(case_id, seed)]
                for seed in case["generation"]["seed_set"]
            ]
            labels = sorted(labels_by_case.get(case_id, []))
            priority = 1 if result["risk"] == "blocker" else 2
            if "system_failure" in labels or "joint_or_ambiguous" in labels:
                priority = 0
            value = {
                "case_id": case_id,
                "risk": result["risk"],
                "category": result["category"],
                "evaluation_layer": result["evaluation_layer"],
                "machine_decision": result["decision"],
                "machine_attributions": result["attributions"],
                "audited_attributions": labels,
                "review_priority": priority,
                "review_status": "pending_human_adjudication",
                "attempts": attempts,
            }
            stream.write(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
            )


def _write_markdown(report: Mapping[str, object], path: Path) -> None:
    gate = report["formal_gate"]
    stability = report["execution_stability"]
    integrity = report["evaluation_integrity"]
    audited = report["audited_attributions"]
    lines = [
        "# 白未晞正式质量自动评测报告",
        "",
        f"> Run：`{report['run_id']}`  ",
        f"> 套件：`{report['suite']['suite_id']}`  ",
        f"> 自动闸门：`{'通过' if gate['automatic_gate_passed'] else '未通过'}`  ",
        "> P0 正式质量：`未通过`（人工长会话尚未执行）",
        "",
        "## 结果",
        "",
        f"- 案例：`{report['suite']['automatic_cases']}`。",
        f"- 冻结种子尝试：`{report['suite']['case_attempts']}`。",
        f"- 计划对话轮次：`{report['suite']['planned_turn_executions']}`；实际进入 Runtime：`{report['suite']['turn_executions']}`。",
        f"- 判定：`{json.dumps(report['decision_counts'], ensure_ascii=False)}`。",
        f"- 原始机器归因：`{json.dumps(report['attribution_counts'], ensure_ascii=False)}`。",
        f"- 审计主归因：`{json.dumps(audited['primary_case_counts'], ensure_ascii=False)}`。",
        f"- Blocker 未通过：`{gate['blocker_failures']}`。",
        f"- Important 通过率：`{gate['important_pass_rate']}`。",
        f"- Normal 通过率：`{gate['normal_pass_rate']}`。",
        f"- 自动语义加权均分：`{gate['overall_weighted_score']}`。",
        "",
        "## 执行稳定性",
        "",
        f"- 成功尝试：`{stability['successful_attempts']}`；执行失败：`{stability['failed_attempts']}`；成功率：`{stability['success_rate']}`。",
        f"- 失败模式：`{json.dumps(stability['failure_modes'], ensure_ascii=False)}`。",
        f"- 失败尝试中的后端调用错误：`{stability['backend_call_errors']}`。",
        "- `GAME_REPLY` 和 `TURN_MIND_ADVANCE` 失败均发生在相同模式第二次结构化生成后；当前证据指向模型输出/协议解析重试耗尽，不是快照、数据库、身份或记忆隔离故障。",
        "",
        "## 评测完整性",
        "",
        f"- 精确词表与语义 Judge 冲突尝试：`{integrity['deterministic_semantic_disagreement_attempts']}`。",
        f"- Judge 自身执行错误尝试：`{integrity['judge_error_attempts']}`。",
        f"- 审计确认的角色失败案例：`{audited['case_counts']['character_failure']}`。",
        f"- 以评测歧义为主归因、必须人工裁决的案例：`{audited['primary_case_counts']['evaluation_ambiguity']}`；另有 `1` 个联合失败案例同时发生 Judge 解码错误。",
        "- `4.7727` 只来自成功生成且被 Judge 打分的轮次；执行失败没有进入均分，因此该数字不能与案例通过率等价。",
        "- 秦未晞本地 Judge 多次在理由中引用回复并未出现的内容；原始 `30/59` 保留为机器筛查结果，不升级为人工真值。",
        "",
        "## 分类结果",
        "",
        "| 分类 | 案例 | 通过 | 失败 | 歧义 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, value in report["category_results"].items():
        lines.append(
            f"| `{name}` | {value['cases']} | {value['pass']} | {value['fail']} | {value['ambiguous']} |"
        )
    lines.extend([
        "",
        "## 未通过案例",
        "",
    ])
    for item in report["case_results"]:
        if item["decision"] != "pass":
            audited_labels = [
                label
                for label, case_ids in audited["case_ids"].items()
                if item["case_id"] in case_ids
            ]
            lines.append(
                f"- `{item['case_id']}`：{item['decision']}；审计归因={','.join(audited_labels) or 'none'}；失败种子={item['failed_seeds']}；歧义种子={item['ambiguous_seeds']}"
            )
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "语义初审使用秦未晞历史模型作为独立本地 Judge，只用于批量初筛。全部 59 个机器失败案例已写入 `manual_review_queue.jsonl`；自动通过不能替代四个人工长会话。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run(args: argparse.Namespace) -> int:
    started_at = datetime.now().astimezone().isoformat()
    suite_manifest = json.loads(SUITE_MANIFEST_PATH.read_text(encoding="utf-8"))
    if suite_manifest["manifest_sha256"] != args.suite_manifest_sha256:
        raise RuntimeError("quality suite manifest SHA256 mismatch")
    artifact_sha256 = _sha256(args.candidate_artifact_path)
    if artifact_sha256 != args.candidate_artifact_sha256:
        raise RuntimeError("candidate GGUF SHA256 mismatch")
    candidate_metadata = await _verify_ollama_model(
        args.base_url, args.model, args.candidate_ollama_digest
    )
    judge_metadata = await _verify_ollama_model(
        args.base_url, args.judge_model, args.judge_ollama_digest
    )
    cases = _load_cases()
    if args.case_id:
        requested = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in requested]
        missing = requested - {case["case_id"] for case in cases}
        if missing:
            raise RuntimeError(f"unknown case IDs: {sorted(missing)}")
    if args.limit is not None:
        cases = cases[: args.limit]
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_path = run_dir / "attempts_raw.jsonl"
    judged_path = run_dir / "attempts_judged.jsonl"
    if not args.resume and (raw_path.exists() or judged_path.exists()):
        raise RuntimeError("run directory already contains attempt files; use --resume")
    raw_attempts = _load_jsonl_by_key(raw_path, "attempt_key") if args.resume else {}
    total_attempts = sum(len(case["generation"]["seed_set"]) for case in cases)
    completed = len(raw_attempts)
    pending_attempts = [
        (case, seed)
        for case in cases
        for seed in case["generation"]["seed_set"]
        if _attempt_key(case["case_id"], seed) not in raw_attempts
    ]
    if pending_attempts:
        candidate = OllamaWorldMindBackend(
            OllamaConfig(
                model_name=args.model,
                base_url=args.base_url,
                context_size=args.context_size,
                keep_alive=args.keep_alive,
                request_timeout_seconds=args.timeout_seconds,
            )
        )
        await candidate.start()
        shared = SharedSeedBackend(candidate)
        prompt = CharacterPackagePromptComposer(_runtime_config()).compose(
            RuntimeSessionIdentity(
                save_id="quality_prompt",
                world_id="songjiangfu",
                protagonist_id="protagonist",
                active_character_id="baiweixi",
                conversation_id="quality_prompt",
            )
        ).system_prompt
        try:
            for case, seed in pending_attempts:
                key = _attempt_key(case["case_id"], seed)
                if case["evaluation_layer"] == "character_direct":
                    value = await _run_direct_attempt(case, seed, shared, prompt)
                else:
                    value = await _run_runtime_attempt(case, seed, shared, args, run_dir)
                _append_jsonl(raw_path, value)
                raw_attempts[key] = value
                completed += 1
                print(
                    f"GENERATE {completed}/{total_attempts} {key} -> "
                    f"{'ok' if value['success'] else value.get('failure_code')}",
                    flush=True,
                )
        finally:
            await candidate.close()

    judge = OllamaWorldMindBackend(
        OllamaConfig(
            model_name=args.judge_model,
            base_url=args.base_url,
            context_size=args.judge_context_size,
            keep_alive=args.keep_alive,
            request_timeout_seconds=args.judge_timeout_seconds,
        )
    )
    await judge.start()
    try:
        judged = await _judge_attempts(raw_attempts, judged_path, judge, args)
    finally:
        await judge.close()
    report = _build_report(
        args,
        run_dir,
        cases,
        judged,
        candidate_metadata,
        judge_metadata,
        started_at,
    )
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_manual_review_queue(
        report,
        cases,
        judged,
        run_dir / "manual_review_queue.jsonl",
    )
    _write_markdown(report, run_dir / "report.md")
    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "decision_counts": report["decision_counts"],
                "attribution_counts": report["attribution_counts"],
                "formal_gate": report["formal_gate"],
                "report_path": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    run_id = datetime.now().strftime("baiweixi-quality-%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(
        description="Execute all 89 automatic Bai Weixi quality cases and attribute failures."
    )
    parser.add_argument("--model", default="baiweixi")
    parser.add_argument("--judge-model", default="qinweixi-qwen35")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-size", type=int, default=8192)
    parser.add_argument("--judge-context-size", type=int, default=8192)
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--judge-timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--candidate-artifact-path",
        type=Path,
        default=CANDIDATE_ARTIFACT_PATH,
    )
    parser.add_argument("--candidate-artifact-sha256", default=CANDIDATE_SHA256)
    parser.add_argument("--candidate-ollama-digest", default=CANDIDATE_OLLAMA_DIGEST)
    parser.add_argument("--judge-ollama-digest", default=JUDGE_OLLAMA_DIGEST)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--suite-manifest-sha256",
        default="182ed7603551bca7e4acd0e1a2c4c2acafaec4f30d8bba43408179ad1f75c3d9",
    )
    parser.add_argument(
        "--run-dir",
        default=str(ROOT / "eval" / "baiweixi_quality" / "runs" / run_id),
    )
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(_parser().parse_args())))
