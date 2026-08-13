"""阶段 5：V4 数据生成通用入口（多角色接线，2026-08-08）。

用法（API key 走环境变量，不落盘）：
  OPENAI_API_KEY=<key> python gen_v4.py --profile qinweixi --count 20 \
      --out data/life_corpus/qin_v4_sample.jsonl

流程：ProfilePackage 编译（按 --profile 从 profiles/<id>/manifest.yaml 发现四类包）
→ 真实模型（OpenAI 兼容 chat 格式）生成 → 门 → ShareGPT 导出。

与 gen_qin_v4.py（秦未晞专用，现为兼容壳）的关系：
  gen_qin_v4.py = 本入口 --profile qinweixi 的别名，保留旧符号导出供工具/测试引用。
新增角色 = 新建 profiles/<id>/ 四件套（manifest/profile/recipe/release + pools），
本入口零改动。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_gen_v4.adapters.exporters.export import ShareGPTReplyExportAdapter
from data_gen_v4.adapters.exporters.reranker_jsonl import RerankerJsonlExportAdapter
from data_gen_v4.adapters.models.pool import ModelPool, OpenAICompatModelAdapter
from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.adapters.modes.rerank import MemoryRerankAdapter
from data_gen_v4.adapters.modes.style import make_style_resolver
from data_gen_v4.adapters.sources.registry import (
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.admission import Freeze02Admission
from data_gen_v4.core.compiler import GenerationPlanCompiler, run_timestamp
from data_gen_v4.core.dataset_generator import DatasetGenerator
from data_gen_v4.core.engine import GenerationEngineV4
from data_gen_v4.core.gates import ReleaseQualityGate, accepted, secret_keyword_checker, safety_action_checker
from data_gen_v4.core.plan import PackageSetV4, RunSpec
from data_gen_v4.core.records import GateDecisionRecord
from data_gen_v4.core.sink import AppendSink

ROOT = Path(__file__).resolve().parent
PROFILES_ROOT = ROOT / "profiles"
# FREEZE-02 冻结合同默认路径（AI 程序侧只读引用；可用 --freeze02-contract 覆盖）
DEFAULT_FREEZE02_CONTRACT = Path(r"F:\AiPeople\eval\training_contract\freeze02_contract_v2.json")

_PACKAGE_KINDS = ("profile", "protocol", "recipe", "release")
_PACKAGE_REF_FIELDS = {
    "profile": "profile_package_ref",
    "protocol": "protocol_bundle_ref",
    "recipe": "dataset_recipe_ref",
    "release": "release_policy_ref",
}


def load_manifest(profiles_root: Path, profile_id: str) -> dict:
    """读 profiles/<id>/manifest.yaml；不存在时显式失败（不静默回退）。"""
    manifest_path = profiles_root / profile_id / "manifest.yaml"
    if not manifest_path.exists():
        known = sorted(
            p.name for p in profiles_root.iterdir()
            if p.is_dir() and (p / "manifest.yaml").exists()
        ) if profiles_root.exists() else []
        raise SystemExit(
            f"[error] 未知 profile: {profile_id}（{manifest_path} 不存在；"
            f"已知: {known or '无' }）"
        )
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8"))


def build_package_set(profiles_root: Path, profile_id: str) -> PackageSetV4:
    """从 manifest 引用的四类 package 文件读 package_id@version，构造 PackageSetV4。

    ref 语法 `pkg:<package_id>@<package_version>`（resolver.py §5 冻结）；
    package 文件路径为相对 profiles/<id>/ 的相对路径（protocol 常指到
    data_gen_v4/packages/ 的共享协议包）。
    """
    manifest = load_manifest(profiles_root, profile_id)
    refs = manifest.get("packages") or {}
    profile_dir = profiles_root / profile_id
    kwargs: dict[str, str] = {}
    for kind in _PACKAGE_KINDS:
        filename = refs.get(kind)
        if not filename:
            raise SystemExit(f"[error] {profile_id}/manifest.yaml 缺少 packages.{kind}")
        pkg_path = (profile_dir / str(filename)).resolve()
        if not pkg_path.exists():
            raise SystemExit(f"[error] {profile_id} 的 {kind} 包文件不存在: {pkg_path}")
        pkg = yaml.safe_load(pkg_path.read_text(encoding="utf-8"))
        pid = pkg.get("package_id")
        pver = pkg.get("package_version")
        if not pid or not pver:
            raise SystemExit(
                f"[error] {pkg_path} 缺少 package_id/package_version（无法构造 ref）"
            )
        kwargs[_PACKAGE_REF_FIELDS[kind]] = f"pkg:{pid}@{pver}"
    return PackageSetV4(**kwargs)


def run_artifacts(
    profile_id: str, count: int, data_dir: Path, out_override: str | None = None
) -> tuple[str, Path, Path]:
    """多角色命名：run_id 前缀与 sqlite/jsonl 输出路径（{profile_id}_v4_{count}.*）。

    run_id 含时间戳（并发区分），调用方只断言前缀；out_override 指定时 jsonl
    路径跟随覆盖，**sqlite 也跟随同名**（2026-08-09 修复：--out 覆盖 jsonl 时
    ledger 按 count 命名会导致多次运行互相覆盖，G7 apply_review 写错 ledger）。
    """
    import time

    run_id = f"{profile_id}-real-{count}-{int(time.time())}"
    if out_override:
        out_path = ROOT / out_override
        sink_path = out_path.with_suffix(".sqlite")
    else:
        sink_path = data_dir / f"{profile_id}_v4_{count}.sqlite"
        out_path = ROOT / f"训练数据/{profile_id}_v4_{count}.jsonl"
    return run_id, sink_path, out_path


def pick_items(plan, count: int):
    """混合类型取 count 条：casual/romance/identity/emotion/protective 各取一部分。

    count >= 全量时直接返回全部（per_type 均分会因某类型不足而截断总量，
    如配额 600/5=120 而 identity 仅 30 条 → 只出 430 条）。
    """
    from collections import Counter

    if count >= len(plan.items):
        return list(plan.items)
    tasks = [i.task_type for i in plan.items]
    dist = Counter(tasks)
    per_type = max(1, count // len(dist))
    picked: list = []
    seen: dict[str, int] = {}
    for item in plan.items:
        seen[item.task_type] = seen.get(item.task_type, 0) + 1
        if seen[item.task_type] <= per_type:
            picked.append(item)
        if len(picked) >= count:
            break
    return picked[:count]


def pick_after_skip(plan, per_type_skip: int):
    """跳过每类型前 per_type_skip 条取剩余（补跑用：与第一次 run 拼接成完整配额）。"""
    picked: list = []
    seen: dict[str, int] = {}
    for item in plan.items:
        task = item.task_type
        seen[task] = seen.get(task, 0) + 1
        if seen[task] > per_type_skip:
            picked.append(item)
    return picked


def pick_remainder(plan, done: dict[str, int]):
    """按类型跳过已生成的条数，取剩余（批次追加：--skip-types casual=250,...）。"""
    picked: list = []
    seen: dict[str, int] = {}
    for item in plan.items:
        task = item.task_type
        seen[task] = seen.get(task, 0) + 1
        if seen[task] > done.get(task, 0):
            picked.append(item)
    return picked


def pick_range(plan, ranges: dict[str, tuple[int, int]]):
    """按类型取 [start, end) 索引段（分批生成：--range-types casual=250:350,...）。"""
    picked: list = []
    seen: dict[str, int] = {}
    for item in plan.items:
        task = item.task_type
        idx = seen.get(task, 0)
        seen[task] = idx + 1
        lo, hi = ranges.get(task, (0, 0))
        if lo <= idx < hi:
            picked.append(item)
    return picked


def pick_topics(plan, topics: dict[str, list[str]]):
    """按话题名精确选（2026-08-10 对话重复方案：配合 topic_ledger 的未用话题，
    绕过索引段限制——未用话题分散时区间会带出已用话题）。"""
    picked: list = []
    seen: set[str] = set()
    for item in plan.items:
        topic = (item.input or {}).get("topic", "")
        if topic not in topics.get(item.task_type, []):
            continue
        if topic in seen:
            continue  # 同话题只取一条
        seen.add(topic)
        picked.append(item)
    return picked


def select_plan_items(plan, args: argparse.Namespace):
    """Apply CLI subset options before FREEZE-02 admission.

    The compiler still validates the full recipe.  DatasetGenerator invokes this selector
    before admission so an unselected mode cannot block an otherwise valid REPLY batch,
    while every item that will actually execute remains covered by the frozen contract.
    """
    if args.retry_from:
        import json as _json
        import sqlite3

        con = sqlite3.connect(args.retry_from)
        try:
            failed_pids: set[str] = set()
            cand_pids: set[str] = set()
            for (payload,) in con.execute(
                "SELECT payload_json FROM v4_records WHERE record_type='failure'"
            ):
                failed_pids.add(_json.loads(payload).get("plan_id"))
            for (payload,) in con.execute(
                "SELECT payload_json FROM v4_records WHERE record_type='candidate'"
            ):
                cand_pids.add(_json.loads(payload).get("plan_id"))
        finally:
            con.close()
        final_failed = sorted(failed_pids - cand_pids)
        indexes = sorted(int(pid.rsplit(":", 1)[-1]) for pid in final_failed if pid)
        if indexes and indexes[-1] >= len(plan.items):
            raise SystemExit(
                f"失败 plan 索引越界: {indexes[-1]} >= {len(plan.items)}"
            )
        selected = [plan.items[i] for i in indexes]
        print(f"重生成失败 items: {len(selected)} 条（索引 {indexes}）")
        return selected
    if args.per_type_skip:
        return pick_after_skip(plan, args.per_type_skip)
    if args.item_skip:
        return list(plan.items[args.item_skip:])
    if args.skip_types:
        done = dict(pair.split("=") for pair in args.skip_types.split(","))
        return pick_remainder(plan, {key: int(value) for key, value in done.items()})
    if args.range_types:
        ranges = {}
        for pair in args.range_types.split(","):
            task, span = pair.split("=")
            lo, hi = span.split(":")
            ranges[task] = (int(lo), int(hi))
        return pick_range(plan, ranges)
    if args.topics:
        topics: dict[str, list[str]] = {}
        for pair in args.topics.split(";"):
            if "=" not in pair:
                continue
            task, names = pair.split("=", 1)
            topics[task] = [name.strip() for name in names.split(",") if name.strip()]
        selected = pick_topics(plan, topics)
        print(f"按话题选: {len(selected)} 条（{sum(len(v) for v in topics.values())} 个话题名）")
        return selected
    if args.regen_indexes:
        indexes = [int(value) for value in args.regen_indexes.split(",")]
        out_of_range = [index for index in indexes if index >= len(plan.items)]
        if out_of_range:
            raise SystemExit(f"重生成索引越界: {out_of_range} >= {len(plan.items)}")
        selected = [plan.items[index] for index in indexes]
        print(f"重生成指定 items: {len(selected)} 条（索引 {indexes}）")
        return selected
    return pick_items(plan, args.count)


# ── 2026-08-09：按 mode 选择 renderer/exporter（REPLY 与 MEMORY_RERANK 分轨）──

def _render_training_for_mode(winner, context, reply_renderer, rerank_renderer,
                              player_name=None, player_age=None):
    """按 winner 的 mode 渲染 TrainingRecord（REPLY → 对话；MEMORY_RERANK → 协议）。

    2026-08-09 称呼注入：player_name 提供时，浅拷贝 context，往锚注入
    anchor_contract.player_name + anchor_facts.player_age → 该训练行锚含
    "玩家叫{名字}，{年龄}岁"关系句（T2 锚统一：推理侧同样渲染）。
    """
    candidate = winner.to_dict()
    mode = candidate.get("mode")
    if mode == "MEMORY_RERANK":
        return rerank_renderer.render_training(candidate, context)
    ctx = context
    if player_name:
        ctx = dict(context)
        profile = dict(context["profile"])
        contract = dict(profile.get("anchor_contract") or {})
        contract["player_name"] = player_name
        profile["anchor_contract"] = contract
        ctx["profile"] = profile
        if context.get("anchor_facts") is not None:
            anchor_facts = dict(context["anchor_facts"])
            anchor_facts["player_age"] = str(player_age or 24)
            ctx["anchor_facts"] = anchor_facts
    return reply_renderer.render_training(candidate, ctx)


def exporter_render(training, export_profile=None) -> str:
    """按 TrainingRecord 的 mode 选择 exporter 序列化。"""
    from data_gen_v4.adapters.exporters.export import ShareGPTReplyExportAdapter

    if getattr(training, "mode", "") == "MEMORY_RERANK":
        from data_gen_v4.adapters.exporters.reranker_jsonl import RerankerJsonlExportAdapter

        return RerankerJsonlExportAdapter().render(training, export_profile)
    return ShareGPTReplyExportAdapter().render(training)


def _training_turns(training) -> int:
    """metadata 的 turns：REPLY 为消息轮数；协议模式为 labels 数（无 messages）。"""
    messages = getattr(training, "messages", None)
    if messages:
        return len(messages)
    return 0


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]", "_", str(text))[:40]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="baiweixi",
                    help="角色包 id（profiles/<id>/manifest.yaml；baiweixi=白未晞当前 P0 女主角；qinweixi=历史角色）")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--workers", type=int, default=8, help="并行工作线程数（API 并发上限未知，429 时调回 5）")
    ap.add_argument(
        "--per-type-skip",
        type=int,
        default=0,
        help="每类型跳过前 N 条再取（补跑模式：与第一次 run 拼成完整配额，如 --per-type-skip 120）",
    )
    ap.add_argument(
        "--retry-from",
        default=None,
        help="sqlite 路径：重生成其中最终失败的 items（与已有 run 拼成完整配额）",
    )
    ap.add_argument(
        "--item-skip",
        type=int,
        default=0,
        help="按序跳过 plan 前 N 个 items 取剩余（批次追加：--item-skip 600 生成第 601-1100 段）",
    )
    ap.add_argument(
        "--skip-types",
        default=None,
        help="按类型跳过已生成条数取剩余（批次追加：--skip-types casual=250,romance=160,identity=30,emotion=100,protective=60）",
    )
    ap.add_argument(
        "--range-types",
        default=None,
        help="按类型取索引区间（分批生成：--range-types casual=250:350,romance=160:240,emotion=100:120）",
    )
    ap.add_argument(
        "--regen-indexes",
        default=None,
        help="按 plan item 全局索引重生成（T14：人工复核 fail/doubt 条目，逗号分隔，如 --regen-indexes 2506,2560）",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="ShareGPT 输出路径（默认 训练数据/{profile}_v4_{count}.jsonl）",
    )
    ap.add_argument("--base-url", default="https://aihub.lmdgame.com/api-product/v1")
    ap.add_argument("--model", default="deepseek-v4-flash")
    ap.add_argument("--api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--freeze02-contract", default=str(DEFAULT_FREEZE02_CONTRACT),
                    help="FREEZE-02 冻结合同路径（默认 AI 程序侧只读契约）")
    ap.add_argument("--skip-admission", action="store_true",
                    help="跳过 FREEZE-02 准入检查（机制验证/协议模式用；"
                         "生产 MEMORY_RERANK 需程序侧 SELECT-01 重排标签合同冻结后才可去掉）")
    # 2026-08-09：称呼注入（名字可配置，多样名字增强）
    ap.add_argument("--player-names", default=None,
                    help="玩家名字集（逗号分隔，如 浩然,陈默,阿伟,林墨；按 item 轮转注入；"
                         "缺省不注入 = 零称呼现状）")
    ap.add_argument("--player-age", type=int, default=24,
                    help="玩家年龄（锚关系句渲染用，默认 24）")
    ap.add_argument("--name-ratio", type=float, default=0.2,
                    help="名字样本比例（0-1，默认 0.2 = 20%% item 注入名字，其余只用'你'）")
    # 2026-08-10：seed 参数化（对话重复根因方案 4）——不同批次用不同 seed，
    # 同话题输出骨架错开，避免每次生成趋同
    ap.add_argument("--seed", type=int, default=42,
                    help="编译/采样种子（默认 42；批次间换 seed 增加多样性）")
    ap.add_argument("--topics", default=None,
                    help="按话题名精确选（2026-08-10：reply_casual=话题1,话题2;reply_romance=...；"
                         "配合 tools/topic_ledger.py --unused 的未用话题，避免区间带出已用）")
    args = ap.parse_args()

    player_names = [n.strip() for n in (args.player_names or "").split(",") if n.strip()]

    api_key = os.getenv(args.api_key_env, "")
    if not api_key:
        print(f"[error] 未配置 {args.api_key_env}（显式失败，不回退 mock）")
        sys.exit(1)

    # 1. 角色包发现 + FREEZE-02 准入（多角色接线：包 refs 由 manifest 推导，零硬编码）
    run_id, sink_path, out_path = run_artifacts(
        args.profile, args.count, ROOT / "训练数据", args.out
    )
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(ROOT)
    package_set = build_package_set(PROFILES_ROOT, args.profile)
    pools_path = PROFILES_ROOT / args.profile / "pools.yaml"
    if not pools_path.exists():
        pools_path = None  # recipe 内嵌 topic_pools / sample_inputs 时可缺
    factory = RecipeDrivenItemFactory(pools_path=str(pools_path) if pools_path else None)
    generator = DatasetGenerator(
        GenerationPlanCompiler(registry, loader, factory),
        admission=None if args.skip_admission else Freeze02Admission(
            contract_path=args.freeze02_contract
        ),
    )
    result = generator.build(
        RunSpec(
            run_id=run_id,
            seed=args.seed,
            model_pin={
                "primary": {
                    "model_id": args.model,
                    "revision": "openai-compat",
                    "sampling": {"temperature": 0.7, "max_tokens": 8192},
                }
            },
            exporter_pin={"primary": {"exporter_id": "sharegpt-reply", "version": "1.0"}},
        ),
        package_set,
        item_selector=lambda plan: select_plan_items(plan, args),
    )
    subset = result.plan
    print(f"编译并准入: {len(subset.items)} items, run={run_id}, lock={result.lock['lock_hash'][:16]}...")

    # 2. 已在 DatasetGenerator 内选出并准入实际执行子集。
    tasks = {i.task_type for i in subset.items}
    print(f"生成子集: {len(subset.items)} 条（类型: {sorted(tasks)}）")

    # 2026-08-09：称呼注入（混合方案）——--player-names 提供时，按 name_ratio
    # 确定性分配（hash(plan_id) % 100 < ratio*100）注入 player_name，
    # 名字按 item 序号轮转（一次 run 含多样名字 → 模型学泛化模式，不绑具体名）。
    # 未注入 item = 现状零称呼（80%）。
    if player_names:
        import hashlib

        injected = 0
        for idx, item in enumerate(subset.items):
            digest = int.from_bytes(
                hashlib.sha256(str(item.plan_id).encode("utf-8")).digest()[:4], "big"
            )
            if digest % 100 >= int(args.name_ratio * 100):
                continue
            name = player_names[idx % len(player_names)]
            item.input["player_name"] = name
            item.input["player_age"] = args.player_age  # 持久化（re_export 复现锚）
            injected += 1
        print(f"称呼注入: {injected}/{len(subset.items)} 条（{injected/len(subset.items)*100:.0f}%，"
              f"名字集: {player_names}）")

    # 3. 真实模型（OpenAI 兼容 chat 格式）；style resolver 从 profile 包构造（多角色）
    adapter = OpenAICompatModelAdapter(
        base_url=args.base_url, api_key_env=args.api_key_env, default_model=args.model
    )
    pool = ModelPool(adapters={"openai-compat": adapter})
    pool.default_id = "openai-compat"
    style_resolver = make_style_resolver(result.context["profile"], ROOT)
    engine = GenerationEngineV4(
        {
            "REPLY": ReplyModeAdapter(
                style_contract_resolver=style_resolver,
                # T12 实测（2026-08-06）：aihub deepseek-v4-flash 禁思考参数为
                # thinking={"type": "disabled"}（reasoning_content 归零，token 全给正文；
                # 此前未禁思考 → 思考吃满预算 → 截断 JSON → 历史 parse_error 672 次的根因）
                semantic_extra_body={"thinking": {"type": "disabled"}},
            ),
            # 2026-08-09：MEMORY_RERANK（记忆选择器训练数据，多角色通用；
            # 生产需程序侧 SELECT-01 重排标签合同冻结，见 --skip-admission）
            "MEMORY_RERANK": MemoryRerankAdapter(),
        },
        package_context=result.context,
    )

    data_dir = ROOT / "训练数据"
    data_dir.mkdir(parents=True, exist_ok=True)

    with AppendSink.open(sink_path) as sink:
        run = engine.execute_parallel(subset, result.lock, pool, sink, workers=args.workers)
        progress = sink.read_progress(subset.run_id)

    print(f"\n完成: completed={run.completed} failed={run.failed} skipped={run.skipped}")
    print(f"模型调用: {len(pool.adapters['openai-compat'].calls)} 次 | 候选: {len(progress.candidates)}")
    if run.failed:
        for failure in progress.failures:
            print(f"  [failure] {failure.error_code}: {failure.reason[:80]}")

    # 4. 导出训练行（T6 质量门：导出前逐条跑门禁，命中即拒绝导出）
    #    2026-08-09：按 mode 选择 renderer/exporter（REPLY → ShareGPT；
    #    MEMORY_RERANK → reranker 记录 JSONL，对齐程序侧 reranker_record.schema.json）
    reply_renderer = ReplyModeAdapter(style_contract_resolver=style_resolver)
    rerank_renderer = MemoryRerankAdapter()
    gate = ReleaseQualityGate(release_policy=result.context["release"])
    secret_terms = tuple(
        (result.context["profile"].get("policy_terms") or {}).get("secret_terms", [])
    )
    gate.set_injected("G5", secret_keyword_checker(secret_terms))
    if not (result.context["protocol"].get("safety_action_rules") or {}):
        print("[warn] protocol 未声明 safety_action_rules——G8 无知识可查（安全门失效风险）")
    gate.set_injected("G8", safety_action_checker())
    with AppendSink.open(sink_path) as _sink:
        _progress = _sink.read_progress(subset.run_id)
        review_decisions = [
            {
                "subject_candidate_record_id": d.subject_candidate_record_id,
                "reviewer": d.reviewer,
                "decision": d.decision,
            }
            for d in _progress.gate_decisions
            if d.gate_id == "G7" and d.decision == "approved"
        ]
    gate_context = {
        "lock": result.lock,
        "snapshots": {
            s["snapshot_id"]: s for s in result.context["snapshots"].values()
        },
        "protocol": result.context["protocol"],
        "profile": result.context["profile"],  # 2026-08-10：judge 角色上下文
        "turn_bounds": (2, 64),
        "gate_decisions": review_decisions,
    }
    gate_report_path = out_path.with_suffix(".gate_report.jsonl")
    metadata_path = out_path.with_suffix(".metadata.jsonl")
    exported = rejected = 0
    leak_counts: dict[str, int] = {}
    from data_gen_v4.core.judge import LLMJudge
    from data_gen_v4.core.selector import DatasetSelector

    judge_config = (result.context["release"] or {}).get("judge_config") or {}
    judge_key_env = judge_config.get("api_key_env", "OPENAI_API_KEY")
    selector = DatasetSelector()
    if os.getenv(judge_key_env):
        # 2026-08-10（方案 A）：judge 模型优先 STRONG_MODEL（教师/judge 分离）——
        # 同模型自评是"越界/秘密"类错误打不出低分的根因之一。
        strong_model = os.getenv("STRONG_MODEL") or ""
        judge_model = (judge_config.get("model_id") or strong_model or args.model)
        selector = DatasetSelector(
            LLMJudge(pool, adapter_id="openai-compat", model_ref=judge_model)
        )
        print(f"[judge] 启用 LLM judge（model={judge_model}"
              f"{'，STRONG_MODEL 分离' if strong_model and strong_model != args.model else ''}）")
    else:
        print("[judge] 未配置 API key——退化'首个合法'选择（无 judge 打分）")
    selection_thresholds = (result.context["release"] or {}).get("selection_thresholds") or {}
    winners_all: list = []
    # MEMORY_RERANK 导出上下文（多角色：character_id/canon 从 profile 读，不写死）
    _profile_pkg = result.context["profile"] or {}
    _canon_refs = _profile_pkg.get("canon_sources") or []
    _canon_snapshot = {}
    if _canon_refs:
        _snap = result.context["snapshots"].get(_canon_refs[0]) or {}
        _canon_snapshot = {
            "snapshot_id": _snap.get("snapshot_id", ""),
            "sha256": str(_snap.get("content_hash", "")).removeprefix("sha256:"),
        }
    with AppendSink.open(sink_path) as sink, \
            gate_report_path.open("w", encoding="utf-8") as report, \
            metadata_path.open("w", encoding="utf-8") as meta, \
            out_path.open("w", encoding="utf-8") as f:
        candidates_by_item: dict[str, list] = {}
        for candidate in progress.candidates:
            candidates_by_item.setdefault(candidate.header.plan_id, []).append(candidate)
        for plan_id, cands in candidates_by_item.items():
            cands.sort(key=lambda c: (c.attempt_no, c.candidate_no))
            item = next(x for x in subset.items if x.plan_id == plan_id)
            passed_candidates: list = []
            for candidate in cands:
                candidate_dict = candidate.to_dict()
                eval_context = dict(gate_context)
                if item.required_review == "full":
                    eval_context["human_review_required"] = True
                decisions = gate.evaluate(candidate_dict, eval_context)
                passed = accepted(decisions)
                record = {
                    "sample_id": candidate.sample_id,
                    "task_type": candidate.task_type,
                    "accepted": passed,
                    "decisions": [
                        {"gate_id": d.gate_id, "decision": d.decision, "reason_codes": d.reason_codes}
                        for d in decisions
                    ],
                }
                report.write(json.dumps(record, ensure_ascii=False) + "\n")
                if passed:
                    passed_candidates.append(candidate)
                else:
                    rejected += 1
                    for d in decisions:
                        for code in d.reason_codes:
                            if code.startswith("secret_leak:"):
                                leak_counts[code] = leak_counts.get(code, 0) + 1
            selection = selector.select_winner(
                [c.to_dict() for c in passed_candidates],
                gate_context,
                selection_thresholds,
            )
            if selection is None:
                continue
            winner = next(
                c for c in passed_candidates
                if c.sample_id == selection.candidate["sample_id"]
            )
            winners_all.append(winner)
            sink.append(
                GateDecisionRecord(
                    header=winner.header.with_record(record_type="gate_decision"),
                    subject_sample_id=winner.sample_id,
                    subject_candidate_record_id=winner.header.record_id,
                    gate_id="release",
                    decision="approved",
                    reason_codes=[selection.reason],
                    validator_id="core-gates",
                    reviewer="gate",
                    reviewed_at=run_timestamp(),
                ),
                json.dumps(
                    [run_id, plan_id, "winner", winner.sample_id],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            training = _render_training_for_mode(
                winner, result.context, reply_renderer, rerank_renderer,
                player_name=item.input.get("player_name"),
                player_age=args.player_age,
            )
            if training.mode == "MEMORY_RERANK":
                export_profile = {
                    "character_id": _profile_pkg.get("profile_id", args.profile),
                    "canon_snapshot": _canon_snapshot,
                    "run_id": run_id,
                    "generator_version": "aipeople-gen-v4",
                    "split": "train",
                    "scenario_family": f"scn:{_slug(item.input.get('topic') or 'rerank')}",
                    "created_at": run_timestamp(),
                }
                exported_lines = exporter_render(training, export_profile)
                f.write(exported_lines + "\n")
                _n_records = len(exported_lines.splitlines())
            else:
                f.write(exporter_render(training) + "\n")
                _n_records = 1
            exported += 1
            meta.write(
                json.dumps(
                    {
                        "sample_id": winner.sample_id,
                        "task_type": winner.task_type,
                        "mode": training.mode,
                        "evidence_state": winner.evidence_state,
                        "desired_policy": winner.desired_policy,
                        "family_id": winner.family_id,
                        "scene": (item.input or {}).get("scene", ""),
                        "topic": (item.input or {}).get("topic", ""),
                        "turns": _training_turns(training),
                        "records": _n_records,
                        "quality": selection.scores,
                        "selection_reason": selection.reason,
                        "split_anchor_ids": list(item.split_anchor_ids),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    print(
        f"质量门: 导出 {exported} / 拒绝 {rejected}（gate 报告 → {gate_report_path.name}）"
    )
    if leak_counts:
        print(f"秘密泄漏统计: {leak_counts}")
    print(f"元数据旁路 → {metadata_path.name}（{exported} 条）")
    print(f"ShareGPT 训练行 → {out_path}")

    # 5. 展示前 5 条（REPLY 显示对话；MEMORY_RERANK 显示 query + 标签摘要）
    display_name = (result.context["profile"] or {}).get("display_name", args.profile)
    print("\n=== 样张（winner 前 5 条）===")
    shown = 0
    for winner in winners_all:
        if shown >= 5:
            break
        item = next(x for x in subset.items if x.plan_id == winner.header.plan_id)
        mode = winner.to_dict().get("mode")
        print(f"\n[{shown+1}] {item.task_type} | {item.input['topic'][:20]} | mode={mode}")
        if mode == "MEMORY_RERANK":
            target = winner.target
            query = target.get("query", {})
            print(f"  query.current_user_message: {query.get('current_user_message', '')[:40]}")
            for label in target.get("labels", [])[:6]:
                print(f"  label {label.get('label')}: {label.get('memory_id')} "
                      f"(score={label.get('relevance_score')}, recall={label.get('should_recall')})")
        else:
            for m in winner.target["messages"]:
                who = "玩家" if m["role"] == "human" else display_name
                print(f"  {who}: {m['content']}")
        shown += 1


if __name__ == "__main__":
    main()
