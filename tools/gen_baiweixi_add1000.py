# -*- coding: utf-8 -*-
"""Generate a resumable, REPLY-only Bai Weixi continuation candidate pool.

The current recipe also contains blocked MEMORY_RERANK work.  This driver compiles the
approved Bai Weixi profile, excludes unresolved/time-jump/full-review items, and invokes
gen_v4.py in independent batches.  Re-running skips batches recorded as completed.

Examples:
  python tools/gen_baiweixi_add1000.py --plan-only
  python tools/gen_baiweixi_add1000.py --seeds 20260813,20260814
  python tools/gen_baiweixi_add1000.py --seeds 20260815 --tasks reply_vague,reply_boundary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory  # noqa: E402
from data_gen_v4.adapters.sources.registry import CompositeSourceLoader, FilePackageRegistry  # noqa: E402
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402
from gen_v4 import PROFILES_ROOT, build_package_set  # noqa: E402

DATA_DIR = ROOT / "训练数据"
DEFAULT_PREFIX = "baiweixi_dialogue_add1000_20260813"
DEFAULT_SEEDS = (20260813, 20260814)

# These subjects are unresolved across the current prose/structured canon, or move the
# static Day-11 autumn relationship into a later season/event.  Excluding them preserves
# the authority conflict for a later human decision instead of silently choosing a side.
EXCLUDED_TERMS = (
    "妖果",
    "上古传承",
    "深山",
    "野外",
    "山林",
    "荒山",
    "大山里",
    "猫形走红",
    "网上火",
    "店的招牌",
    "过年",
    "春节",
    "跨年",
    "夏夜",
    "夏天",
    "冬天",
    "生日",
    "周年",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _item_text(item: Any) -> str:
    data = item.input or {}
    return " ".join(str(data.get(key, "")) for key in ("topic", "scene", "player_view"))


def _exclusion_reason(item: Any) -> str | None:
    if item.mode != "REPLY":
        return f"mode:{item.mode}"
    if item.required_review == "full":
        return "human_review:full"
    text = _item_text(item)
    return next((f"term:{term}" for term in EXCLUDED_TERMS if term in text), None)


def compile_safe_indexes(seed: int) -> tuple[list[tuple[int, Any]], Counter[str]]:
    compiler = GenerationPlanCompiler(
        FilePackageRegistry(PROFILES_ROOT),
        CompositeSourceLoader(ROOT),
        RecipeDrivenItemFactory(
            pools_path=str(PROFILES_ROOT / "baiweixi" / "pools.yaml")
        ),
    )
    result = compiler.compile(
        RunSpec(run_id=f"baiweixi-add1000-plan-{seed}", seed=seed),
        build_package_set(PROFILES_ROOT, "baiweixi"),
    )
    safe: list[tuple[int, Any]] = []
    excluded: Counter[str] = Counter()
    for index, item in enumerate(result.plan.items):
        reason = _exclusion_reason(item)
        if reason:
            excluded[reason] += 1
        else:
            safe.append((index, item))
    return safe, excluded


def _chunks(rows: list[tuple[int, Any]], size: int) -> list[list[tuple[int, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "batches": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(path: Path, state: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--seeds", default=",".join(str(seed) for seed in DEFAULT_SEEDS))
    parser.add_argument("--tasks", help="Optional comma-separated task_type allowlist")
    parser.add_argument("--batch-size", type=int, default=75)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-run completed batches")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 100:
        raise SystemExit("[error] --batch-size must be between 1 and 100")

    _load_dotenv(ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY") and not args.plan_only:
        raise SystemExit("[error] OPENAI_API_KEY is not configured")
    base_url = args.base_url or os.getenv("OPENAI_BASE_URL") or "https://aihub.lmdgame.com/api-product/v1"
    seeds = [int(value.strip()) for value in args.seeds.split(",") if value.strip()]
    task_filter = {value.strip() for value in (args.tasks or "").split(",") if value.strip()}
    state_path = DATA_DIR / f"{args.prefix}.run_state.json"
    state = _load_state(state_path)
    state.update(
        {
            "profile_id": "baiweixi",
            "dataset_family": "visible_reply",
            "mode": "REPLY",
            "prefix": args.prefix,
            "updated_at": _now(),
            "exclusion_terms": list(EXCLUDED_TERMS),
        }
    )
    state.setdefault("batches", {})

    planned: list[tuple[int, int, list[tuple[int, Any]]]] = []
    for seed in seeds:
        safe, excluded = compile_safe_indexes(seed)
        if task_filter:
            safe = [(index, item) for index, item in safe if item.task_type in task_filter]
        distribution = Counter(item.task_type for _, item in safe)
        print(f"seed={seed}: safe={len(safe)} distribution={dict(distribution)}")
        print(f"  excluded={dict(excluded)}")
        for batch_no, chunk in enumerate(_chunks(safe, args.batch_size), start=1):
            planned.append((seed, batch_no, chunk))
    print(f"planned batches={len(planned)}, items={sum(len(chunk) for _, _, chunk in planned)}")
    if args.plan_only:
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for ordinal, (seed, batch_no, chunk) in enumerate(planned, start=1):
        batch_id = f"s{seed}_b{batch_no:03d}"
        stem = f"{args.prefix}_{batch_id}"
        out = DATA_DIR / f"{stem}.jsonl"
        prior = state["batches"].get(batch_id) or {}
        if prior.get("status") == "completed" and not args.force:
            print(f"[{ordinal}/{len(planned)}] skip completed {batch_id}: exported={prior.get('exported', 0)}")
            continue
        indexes = ",".join(str(index) for index, _ in chunk)
        cmd = [
            sys.executable,
            str(ROOT / "gen_v4.py"),
            "--profile",
            "baiweixi",
            "--count",
            str(len(chunk)),
            "--workers",
            str(args.workers),
            "--base-url",
            base_url,
            "--model",
            args.model,
            "--seed",
            str(seed),
            "--regen-indexes",
            indexes,
            "--out",
            str(out.relative_to(ROOT)),
        ]
        print(f"[{ordinal}/{len(planned)}] run {batch_id}: items={len(chunk)}")
        started_at = _now()
        proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy())
        exported = _line_count(out)
        metadata = out.with_suffix(".metadata.jsonl")
        state["batches"][batch_id] = {
            "status": "completed" if proc.returncode == 0 else "failed",
            "seed": seed,
            "batch_no": batch_no,
            "task_distribution": dict(Counter(item.task_type for _, item in chunk)),
            "plan_indexes": [index for index, _ in chunk],
            "requested": len(chunk),
            "exported": exported,
            "started_at": started_at,
            "finished_at": _now(),
            "returncode": proc.returncode,
            "data_file": out.name,
            "metadata_file": metadata.name,
            "data_sha256": _sha256(out) if out.exists() else None,
        }
        state["updated_at"] = _now()
        _write_state(state_path, state)
        if proc.returncode != 0:
            print(f"[error] batch failed: {batch_id}; state saved to {state_path}")
            return proc.returncode
        if exported != _line_count(metadata):
            print(f"[error] data/metadata mismatch in {batch_id}")
            return 2
        print(f"  exported={exported}/{len(chunk)}")

    completed = [row for row in state["batches"].values() if row.get("status") == "completed"]
    print(
        f"complete: batches={len(completed)}, requested={sum(row['requested'] for row in completed)}, "
        f"exported={sum(row['exported'] for row in completed)}"
    )
    print(f"state: {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
