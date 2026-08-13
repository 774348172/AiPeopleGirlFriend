"""GenerationEngineV4（《数据生成器v4设计》§7.2、§13）。

execute(plan, package_lock, model_pool, sink)  新 run 全量执行。
resume(run_id, plan, package_lock, model_pool, sink)  中断后续跑。

不变量：
- run 记录、plan、package_lock 必须完全匹配，resume 不允许换 profile/协议/recipe。
- 引擎只产出 pending candidate，不批准样本；不调用训练、切分或封存测试。
- 幂等键 (run_id, plan_id, attempt_no, candidate_no, stage)，重复 append 返回已有记录。
- 重试耗尽后写 failure，不返回 mock。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from .errors import (
    LockMismatchError,
    ModeNotRegisteredError,
    PreconditionFailedError,
    V4Error,
    V4ErrorCode,
)
from .interfaces import ModeAdapter, ModelPool
from .compiler import derive_seed
from .plan import GenerationPlanV4, PlanItemV4
from .records import (
    CallRecord,
    CandidateRecordV4,
    FailureRecord,
    LineageHeaderV4,
    RunEvent,
)
from .sink import AppendSink, MemorySink, RunProgress

_STAGE_RUN = "run_started"
_STAGE_COMPLETED = "generation_completed"
_STAGE_CANDIDATE = "candidate"
_STAGE_FAILURE = "failure"

# §13.2 标准错误码（FailureRecord.error_code 合法枚举；INTERNAL 不在此列）
_FAILURE_ERROR_CODES = {
    "package_invalid", "package_incompatible", "source_snapshot_failed",
    "precondition_failed", "mode_not_registered", "provider_error", "timeout",
    "truncated_output", "parse_error", "schema_error", "unsupported_claim",
    "policy_mismatch", "style_failure", "no_acceptable_candidate",
}


@dataclass(frozen=True, slots=True)
class GenerationRunResult:
    run_id: str
    plan_id: str
    completed: int
    failed: int
    skipped: int
    generation_completed: bool


class CallExecutor:
    """注入给 ModeAdapter 的模型调用执行器：解析 ModelAdapter、追加 CallRecord、
    执行超时/重试（§7.3）。三次失败后显式抛错，不回退 mock。

    T4 错误分类（2026-08-06）：retryable=False（计费/鉴权/环境）fail-fast 不重试；
    retryable=True/None（限流/5xx/超时/空内容/内容错误）最多重试 3 次，带指数退避。
    """

    def __init__(
        self,
        model_pool: ModelPool,
        sink: AppendSink | MemorySink,
        header: LineageHeaderV4,
        *,
        max_consecutive_failures: int = 3,
        retry_base_delay_seconds: float = 0.5,
    ) -> None:
        self._model_pool = model_pool
        self._sink = sink
        self._header = header
        self._max_failures = max_consecutive_failures
        self._retry_base_delay = retry_base_delay_seconds
        # 大块 B（阶段 2 P0-3）：本次 generate 期间落盘的 CallRecord.record_id 列表，
        # adapter 通过 record_ids() 回链到 candidate.provenance.calls
        self._call_record_ids: list[str] = []

    def record_ids(self) -> list[str]:
        """本次调用序列的 CallRecord.record_id（按调用顺序）。"""
        return list(self._call_record_ids)

    def call(
        self,
        spec: dict[str, Any],
        *,
        stage: str,
        attempt_no: int,
        candidate_no: int | None = None,
    ) -> dict[str, Any]:
        """执行一次模型调用并追加 CallRecord（成功存原始响应，失败也落盘）。返回 ModelCallResult。"""
        adapter = self._model_pool.resolve(spec)
        key = _key(self._header.run_id, self._header.plan_id, attempt_no, candidate_no, stage)
        last_error: Exception | None = None
        for failure_no in range(self._max_failures):
            try:
                result = adapter.generate(spec)
            except Exception as error:  # noqa: BLE001
                last_error = error
                # 大块 B：transient provider error 也落盘（finish_reason=error:<code>，
                # content 空）——调用轨迹进 ledger，与 semantic failure 分开可审计。
                # key 带失败序号：同 call 的多次失败尝试各自落盘（created_at 差异
                # 不触发幂等冲突）
                error_code = getattr(error, "code", None)
                code_name = getattr(error_code, "value", type(error).__name__)
                self._append_call_record(
                    spec, attempt_no, candidate_no, stage,
                    output_hash="", finish_reason=f"error:{code_name}", content="",
                    key_suffix=f"fail{failure_no}",
                )
                if _is_non_retryable(error):
                    # 计费/鉴权/环境错误：重试无意义，立即失败（不浪费额度与时间）
                    raise V4Error(
                        f"模型调用失败（不可重试）: {error}",
                        code=V4ErrorCode.PROVIDER_ERROR,
                        retryable=False,
                    ) from error
                if failure_no < self._max_failures - 1:
                    _sleep_retry(failure_no, self._retry_base_delay)
                continue
            self._append_call_record(
                spec, attempt_no, candidate_no, stage,
                output_hash=_hash_of(result.get("content")),
                finish_reason=str(result.get("finish_reason", "")),
                content=str(result.get("content", "")),
            )
            return result
        raise V4Error(
            f"模型调用失败（{self._max_failures} 次）: {last_error}",
            code=V4ErrorCode.PROVIDER_ERROR,
        )

    def _append_call_record(
        self,
        spec: dict[str, Any],
        attempt_no: int,
        candidate_no: int | None,
        stage: str,
        *,
        output_hash: str,
        finish_reason: str,
        content: str,
        key_suffix: str = "",
    ) -> None:
        call_record = CallRecord(
            header=self._header.with_record(record_type="call"),
            attempt_no=attempt_no,
            candidate_no=candidate_no,
            stage=stage,
            model=spec.get("model", {}),
            prompt_hash=spec.get("prompt_hash", ""),
            input_hash=_hash_of(spec.get("input")),
            output_hash=output_hash,
            finish_reason=finish_reason,
            seed=spec.get("seed", "unsupported"),
            content=content,
        )
        self._call_record_ids.append(call_record.header.record_id)
        key = _key(self._header.run_id, self._header.plan_id, attempt_no, candidate_no, stage)
        if key_suffix:
            key = key[:-1] + f",{key_suffix}]"
        self._sink.append(call_record, key)


def _is_non_retryable(error: Exception) -> bool:
    """V4Error.retryable=False 显式声明为不可重试；其余（含 None）可重试。"""
    if isinstance(error, V4Error):
        return error.retryable is False
    return False


def _sleep_retry(failure_no: int, base_delay: float) -> None:
    import time

    time.sleep(base_delay * (2 ** failure_no))


def _key(run_id: str, plan_id: str | None, attempt_no: int | None,
         candidate_no: int | None, stage: str) -> str:
    import json

    return json.dumps(
        [run_id, plan_id, attempt_no, candidate_no, stage],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _hash_of(value: Any) -> str:
    import json

    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class GenerationEngineV4:
    def __init__(
        self,
        mode_adapters: dict[str, ModeAdapter],
        *,
        generator_version: str = "0.1.0",
        package_context: dict[str, Any] | None = None,
    ) -> None:
        self._mode_adapters = mode_adapters
        self._generator_version = generator_version
        # 编译上下文（profile/protocol/recipe/release/snapshots/facts），
        # 由调用方从 CompileResult.context 传入，adapter 通过 package_set 读取
        self._package_context = package_context or {}

    # ───────────────────────── 执行 ─────────────────────────

    def execute(
        self,
        plan: GenerationPlanV4,
        package_lock: dict[str, Any],
        model_pool: ModelPool,
        sink: AppendSink | MemorySink,
    ) -> GenerationRunResult:
        self._assert_lock_matches(plan, package_lock)
        progress = sink.read_progress(plan.run_id)
        if progress.run_started:
            raise PreconditionFailedError(
                f"run 已存在: {plan.run_id}，请用 resume 续跑或创建新 run"
            )
        self._append_run_event(sink, plan, "run_started")
        result = self._run_items(plan, package_lock, model_pool, sink)
        self._append_run_event(sink, plan, "generation_completed")
        return GenerationRunResult(
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            completed=result.completed,
            failed=result.failed,
            skipped=result.skipped,
            generation_completed=True,
        )

    def execute_parallel(
        self,
        plan: GenerationPlanV4,
        package_lock: dict[str, Any],
        model_pool: ModelPool,
        sink: AppendSink | MemorySink,
        *,
        workers: int = 4,
    ) -> GenerationRunResult:
        """并行执行：工作线程各自用 MemorySink 收集（模型调用并行、AppendSink
        保持单写者），主线程按 item 顺序合并落盘。生成语义与 execute 一致。

        动态任务队列（item 粒度）：静态 chunk 下若某 chunk 内重试较多会拖慢
        整体（等最慢的 worker）；按 item 提交由线程池调度，重试只影响自身。
        """
        import concurrent.futures

        self._assert_lock_matches(plan, package_lock)
        progress = sink.read_progress(plan.run_id)
        if progress.run_started:
            raise PreconditionFailedError(
                f"run 已存在: {plan.run_id}，请用 resume 续跑或创建新 run"
            )
        self._append_run_event(sink, plan, "run_started")

        def run_item(item: Any) -> MemorySink:
            worker_sink = MemorySink()
            subset = replace(plan, items=[item])
            self._run_items(subset, package_lock, model_pool, worker_sink)
            return worker_sink

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_by_item = {
                executor.submit(run_item, item): item for item in plan.items
            }
            sink_by_item: dict[str, MemorySink] = {}
            for future in concurrent.futures.as_completed(future_by_item):
                sink_by_item[future_by_item[future].plan_id] = future.result()

        # 主线程按 plan 顺序合并（幂等键保留，重复提交返回已有）
        for item in plan.items:
            for key, record in sink_by_item[item.plan_id].items():
                sink.append(record, key)

        progress = sink.read_progress(plan.run_id)
        # 修复（2026-08-08 第一批实测）：K 候选下 completed 应为 item 级
        # （completed_plan_ids = 有候选的 item），否则 completed>items 导致 skipped 负数
        completed = len(progress.completed_plan_ids)
        # 只统计最终失败的 item（重试后成功的 item 也有 failure 记录，须排除）
        failed = len(progress.failed_plan_ids - progress.completed_plan_ids)
        skipped = len(plan.items) - completed - failed
        self._append_run_event(sink, plan, "generation_completed")
        return GenerationRunResult(
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            completed=completed,
            failed=failed,
            skipped=skipped,
            generation_completed=True,
        )

    def resume(
        self,
        run_id: str,
        plan: GenerationPlanV4,
        package_lock: dict[str, Any],
        model_pool: ModelPool,
        sink: AppendSink | MemorySink,
    ) -> GenerationRunResult:
        progress = sink.read_progress(run_id)
        if not progress.run_started:
            raise PreconditionFailedError(f"run 不存在: {run_id}")
        if progress.generation_completed:
            raise PreconditionFailedError(f"run 已完成: {run_id}")
        self._assert_lock_matches(plan, package_lock)
        # run 锚定校验：run_started 记录的 plan 与 lock 必须与传入 plan 完全一致，
        # 即使尚无 candidate/failure 记录（§7.2 resume 不变量）。
        if progress.run_started_plan_id != plan.plan_id:
            raise LockMismatchError(
                f"run 锚定 plan {progress.run_started_plan_id!r}，传入 plan {plan.plan_id!r}"
            )
        if progress.run_started_lock_hash != plan.package_lock_hash:
            raise LockMismatchError("run 锚定 lock 与传入 plan 不一致")
        # resume 不变量：run 记录、plan、lock 完全匹配
        run_records = [*progress.candidates, *progress.failures]
        for record in run_records:
            if record.header.plan_id not in {item.plan_id for item in plan.items}:
                raise LockMismatchError(
                    f"run 记录引用未知 plan_id: {record.header.plan_id}"
                )
            if record.header.package_lock_hash != plan.package_lock_hash:
                raise LockMismatchError("run 记录 package_lock_hash 与 plan 不一致")
            if record.header.profile_id != plan.profile_id:
                raise LockMismatchError("resume 不允许更换 profile")
        result = self._run_items(plan, package_lock, model_pool, sink, run_id=run_id)
        self._append_run_event(sink, plan, "generation_completed")
        return GenerationRunResult(
            run_id=run_id,
            plan_id=plan.plan_id,
            completed=result.completed,
            failed=result.failed,
            skipped=result.skipped,
            generation_completed=True,
        )

    # ───────────────────────── 内部 ─────────────────────────

    @staticmethod
    def _assert_lock_matches(plan: GenerationPlanV4, package_lock: dict[str, Any]) -> None:
        if package_lock.get("lock_hash") != plan.package_lock_hash:
            raise LockMismatchError(
                "package_lock 与 plan 不一致："
                f"lock {package_lock.get('lock_hash')!r} vs plan {plan.package_lock_hash!r}"
            )

    def _run_items(
        self,
        plan: GenerationPlanV4,
        package_lock: dict[str, Any],
        model_pool: ModelPool,
        sink: AppendSink | MemorySink,
        *,
        run_id: str | None = None,
    ) -> GenerationRunResult:
        run_id = run_id or plan.run_id
        progress = sink.read_progress(run_id)
        completed = failed = skipped = 0
        for item in plan.items:
            # 大块 B（阶段 2 P0-4）：per-candidate 进度——item 全部 candidate_no
            # 已完成才跳过；部分完成由 _process_item 内部补缺失候选
            k = max(1, int(item.candidate_count))
            done = {c for pid, c in progress.completed_candidate_keys if pid == item.plan_id}
            if set(range(1, k + 1)) <= done:
                skipped += 1
                continue
            outcome = self._process_item(
                plan, item, package_lock, model_pool, sink,
                run_id=run_id, progress=progress,
            )
            if outcome:
                completed += 1
            else:
                failed += 1
        return GenerationRunResult(
            run_id=run_id,
            plan_id=plan.plan_id,
            completed=completed,
            failed=failed,
            skipped=skipped,
            generation_completed=False,
        )

    def _process_item(
        self,
        plan: GenerationPlanV4,
        item: PlanItemV4,
        package_lock: dict[str, Any],
        model_pool: ModelPool,
        sink: AppendSink | MemorySink,
        *,
        run_id: str,
        progress: RunProgress | None = None,
    ) -> bool:
        adapter = self._mode_adapters.get(item.mode)
        if adapter is None:
            raise ModeNotRegisteredError(f"engine 未注册 mode adapter: {item.mode}")
        header = LineageHeaderV4(
            record_type="run",
            run_id=run_id,
            plan_id=item.plan_id,
            package_lock_hash=plan.package_lock_hash,
            profile_id=plan.profile_id,
            profile_snapshot_id=plan.profile_snapshot_id,
            protocol_bundle_id=plan.protocol_bundle_id,
            recipe_id=plan.recipe_id,
            mode=item.mode,
            task_type=item.task_type,
            family_id=item.family_id,
            generator_version=self._generator_version,
        )
        call_executor = CallExecutor(model_pool, sink, header)
        done = {
            c
            for pid, c in (progress.completed_candidate_keys if progress else frozenset())
            if pid == item.plan_id
        }
        k = max(1, int(item.candidate_count))
        produced = 0
        for candidate_no in range(1, k + 1):
            # per-candidate resume：已有候选跳过（ledger 重放 0 重新请求）
            if candidate_no in done:
                produced += 1
                continue
            last_failure_info: dict | None = None
            last_failure_record_id: str | None = None
            for attempt_no in range(1, item.max_attempts + 1):
                # attempt_no/candidate_no 必须进入 item dict（adapter 构造幂等键，§13.1）
                item_dict = item.to_dict()
                item_dict["attempt_no"] = attempt_no
                item_dict["candidate_no"] = candidate_no
                # 大块 B（P0-4）：候选多样性——每 candidate 独立 seed（采样不同）
                item_dict["seed"] = derive_seed(int(item.seed), f"k{candidate_no}")
                if last_failure_info is not None:
                    # 带反馈重试：把上次失败原因传给 adapter，注入重试 prompt
                    item_dict["_last_failure"] = last_failure_info
                job = adapter.prepare(item_dict, package_set=self._package_context)
                try:
                    payload = adapter.generate(job, call_executor)
                except V4Error as error:
                    # adapter 内部失败显式转为 failure（§13.2）；内部错误（非标准
                    # failure code）冒泡暴露编程缺陷，不吞掉。
                    # 基础设施失败（provider_error/timeout 等）在 CallExecutor 内已重试
                    # 3 次，不再进入 engine 的 attempt 重试（避免 3×3 重试爆炸）。
                    if error.code.value not in _FAILURE_ERROR_CODES:
                        raise
                    retryable = error.code.value in ("parse_error", "schema_error",
                                                     "style_failure", "unsupported_claim",
                                                     "policy_mismatch", "no_acceptable_candidate")
                    payload = {
                        "mode_failure": True,
                        "error_code": error.code.value,
                        "retryable": retryable,
                        "reason": str(error),
                    }
                if _is_mode_failure(payload):
                    failure = FailureRecord(
                        header=header.with_record(record_type="failure"),
                        attempt_no=attempt_no,
                        candidate_no=candidate_no,
                        stage="generate",
                        error_code=payload.get("error_code", "no_acceptable_candidate"),
                        retryable=bool(payload.get("retryable", False)),
                        reason=payload.get("reason", ""),
                    )
                    sink.append(
                        failure,
                        _key(run_id, item.plan_id, attempt_no, candidate_no, _STAGE_FAILURE),
                    )
                    if not payload.get("retryable") or attempt_no == item.max_attempts:
                        break  # 该 candidate 耗尽尝试，进入下一个 candidate_no
                    last_failure_info = {
                        "error_code": payload.get("error_code", ""),
                        "reason": payload.get("reason", ""),
                    }
                    last_failure_record_id = failure.header.record_id
                    continue
                candidate = self._wrap_candidate(
                    plan, item, header, payload, attempt_no, candidate_no,
                    parent_record_id=last_failure_record_id,
                    seed_override=item_dict["seed"],
                )
                sink.append(
                    candidate,
                    _key(run_id, item.plan_id, attempt_no, candidate_no, _STAGE_CANDIDATE),
                )
                produced += 1
                break
        return produced > 0

    @staticmethod
    def _wrap_candidate(
        plan: GenerationPlanV4,
        item: PlanItemV4,
        header: LineageHeaderV4,
        payload: dict[str, Any],
        attempt_no: int,
        candidate_no: int,
        *,
        parent_record_id: str | None = None,
        seed_override: int | str | None = None,
    ) -> CandidateRecordV4:
        # 大块 B（阶段 2）：candidate_no 参数化（K 候选）；parent_record_id 记录
        # repair 链（attempt>1 时指向同 item 前一 attempt 的失败记录，parent 不可变）
        return CandidateRecordV4(
            header=header.with_record(record_type="candidate"),
            sample_id=f"{item.plan_id}:a{attempt_no}:c{candidate_no}",
            family_id=item.family_id,
            question_family_id=item.question_family_id,
            scene_family_id=item.scene_family_id,
            mode=item.mode,
            task_type=item.task_type,
            profile_id=plan.profile_id,
            profile_snapshot_id=plan.profile_snapshot_id,
            protocol_bundle_id=plan.protocol_bundle_id,
            recipe_id=plan.recipe_id,
            attempt_no=attempt_no,
            candidate_no=candidate_no,
            parent_sample_id=parent_record_id,
            fixture_id=item.fixture_id,
            fixture_hash=item.fixture_hash,
            source_refs=payload.get("source_refs", item.source_refs),
            source_event_ids=payload.get("source_event_ids", []),
            support_spans=payload.get("support_spans", item.support_spans),
            knowledge_scope=item.knowledge_scope,
            visibility_scope=item.visibility_scope,
            evidence_state=item.evidence_state,
            desired_policy=item.desired_policy,
            required_behaviors=item.required_behaviors,
            forbidden_behaviors=item.forbidden_behaviors,
            expected_outcomes=item.expected_outcomes,
            input=payload["input"],
            target=payload["target"],
            model=payload.get("model", {}),
            prompt_hash=payload.get("prompt_hash", ""),
            prompt_template_version=payload.get("prompt_template_version", item.prompt_template_version),
            config_hash=payload.get("config_hash", item.config_hash),
            render_profile_id=item.render_profile_id,
            representation_ids=payload.get("representation_ids", item.representation_ids),
            seed=seed_override if seed_override is not None else payload.get("seed", item.seed),
            provenance={"calls": payload.get("calls", [])},
            split_anchor_ids=list(item.split_anchor_ids),
        )

    def _append_run_event(
        self,
        sink: AppendSink | MemorySink,
        plan: GenerationPlanV4,
        event_name: str,
    ) -> None:
        header = LineageHeaderV4(
            record_type="run",
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            package_lock_hash=plan.package_lock_hash,
            profile_id=plan.profile_id,
            profile_snapshot_id=plan.profile_snapshot_id,
            protocol_bundle_id=plan.protocol_bundle_id,
            recipe_id=plan.recipe_id,
            generator_version=self._generator_version,
        )
        record = RunEvent(
            header=header.with_record(record_type="run"), event_name=event_name
        )
        sink.append(record, _key(plan.run_id, plan.plan_id, None, None, event_name))


def _is_mode_failure(payload: dict[str, Any]) -> bool:
    return bool(payload.get("mode_failure", False))


def _chunk_items(items: list[Any], workers: int) -> list[list[Any]]:
    """按顺序均分 items 到 workers 个 chunk（保持 plan 内顺序稳定）。"""
    workers = max(1, min(workers, len(items)))
    chunks: list[list[Any]] = [[] for _ in range(workers)]
    for index, item in enumerate(items):
        chunks[index % workers].append(item)
    return [c for c in chunks if c]
