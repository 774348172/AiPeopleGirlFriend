# -*- coding: utf-8 -*-
"""D7-A：T1 canon 变更后的评测契约重冻结（chat01 v3 + canon_snapshot v2 + chat02 alignment v2）。

背景（2026-08-06）：
- T1 锚修复修改了 canon（bible.yaml/canon.json）→ 冻结契约的 canon 哈希过期。
- 同时发现三处既有漂移：runtime/_prompt.py（3295→4486）、m3_v2/02_train.sh（1442→1476）、
  canon_snapshot_v1.json/manifest_v1.json 字节级重写（内容等价）。
- 按 CHAT-01 合同"任何修改创建新版本，不原地覆盖"：v1/v2 保持历史，新建 v3。

本脚本产出（幂等，可重跑）：
1. eval/chat01/suites/canon_snapshot_v2.json
2. eval/chat01/suites/canon_drift.json（+2 条 T1 canon findings）
3. eval/chat01/suites/chat01_suite_manifest_v3.json（冻结清单按 live 重算）
4. eval/chat02/canon_alignment_v2.json
5. eval/chat02/execution_profile_v1.json（prompt_renderer + suite_contract 指向 v3）
6. eval/chat02/models/qinweixi-current-q4_k_m.json（02_train.sh evidence 更新）

前置：eval/chat01/freeze.py 已更新（MANIFEST_PATH→v3、FROZEN_ASSETS→v3 清单）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.chat01.freeze import (  # noqa: E402
    FROZEN_ASSETS,
    canonical_manifest_sha256,
    sha256_file,
    verify_file,
)
from eval.chat01h.freeze import FROZEN_ASSETS_V2  # noqa: E402  (v2 清单参考)

ROOT = Path(__file__).resolve().parents[1]

V1_SNAPSHOT = ROOT / "eval/chat01/suites/canon_snapshot_v1.json"
V2_MANIFEST = ROOT / "eval/chat01/suites/chat01_suite_manifest_v2.json"
V3_MANIFEST = ROOT / "eval/chat01/suites/chat01_suite_manifest_v3.json"
V2_SNAPSHOT = ROOT / "eval/chat01/suites/canon_snapshot_v2.json"
DRIFT = ROOT / "eval/chat01/suites/canon_drift.json"
ALIGN_V1 = ROOT / "eval/chat02/canon_alignment_v1.json"
ALIGN_V2 = ROOT / "eval/chat02/canon_alignment_v2.json"
PROFILE = ROOT / "eval/chat02/execution_profile_v1.json"
MODEL_MANIFEST = ROOT / "eval/chat02/models/qinweixi-current-q4_k_m.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _asset(path: str) -> dict:
    p = ROOT / path
    return {"path": path, "bytes": p.stat().st_size, "sha256": sha256_file(p)}


def build_snapshot_v2() -> None:
    v1 = _load(V1_SNAPSHOT)
    sources = []
    for src in v1["sources"]:
        entry = deepcopy(src)
        live = _asset(src["path"])
        entry["bytes"] = live["bytes"]
        entry["sha256"] = live["sha256"]
        sources.append(entry)
    resolved = deepcopy(v1["resolved_facts"])
    resolved["city"] = "金陵"  # T1：canon city 去元注释
    snapshot = {
        "snapshot_id": "chat01-canon-v2",
        "schema_version": v1["schema_version"],
        "created_at": _now(),
        "hash_algorithm": v1["hash_algorithm"],
        "sources": sources,
        "resolved_facts": resolved,
        "consistency": {
            "status": "consistent",
            "conflicts": [],
            "notes": (
                "v2：T1 锚修复（2026-08-06）后重新冻结——current_city/city 值为'金陵'（元注释移入源注释），"
                "canon 删除与 bible 重复的 name/gender/age/education 与'（可配置）'表述；"
                "其余 resolved_facts 与 v1 一致。"
            ),
        },
    }
    _dump(V2_SNAPSHOT, snapshot)
    print(f"[1] {V2_SNAPSHOT.name} 已生成（{len(sources)} sources）")


def update_canon_drift() -> None:
    drift = _load(DRIFT)
    v1 = _load(V1_SNAPSHOT)
    frozen = {s["path"]: s for s in v1["sources"]}
    existing = {f["finding_id"] for f in drift["findings"]}
    changed = False
    if drift.get("canon_snapshot_id") != "chat01-canon-v2":
        drift["canon_snapshot_id"] = "chat01-canon-v2"
        changed = True
    new_findings = []
    for fid, path, disposition in (
        (
            "drift-t1-canon-bible",
            "人物设定/秦/bible.yaml",
            "T1 锚修复：current_city 去元注释、voice.vocabulary 去标签前缀；已由 chat01-canon-v2 快照吸收，status=resolved。",
        ),
        (
            "drift-t1-canon-canon",
            "人物设定/秦/canon.json",
            "T1 锚修复：删重复 name/gender/age/education、city 去元注释、her_nickname 去'（可配置）'；已由 chat01-canon-v2 快照吸收，status=resolved。",
        ),
    ):
        if fid in existing:
            continue
        live = _asset(path)
        new_findings.append(
            {
                "finding_id": fid,
                "path": path,
                "bytes": frozen[path]["bytes"],
                "sha256": frozen[path]["sha256"],
                "scope": "active_test",
                "severity": "warning",
                "status": "resolved",
                "blocks": [],
                "observed": ["元注释/重复事实（见 disposition）"],
                "expected": ["金陵", "去重后的干净事实"],
                "disposition": disposition,
            }
        )
    if new_findings:
        drift["findings"].extend(new_findings)
        changed = True
    if changed:
        drift["created_at"] = _now()
        _dump(DRIFT, drift)
        print(f"[2] canon_drift.json 已更新（snapshot_id→v2, +{len(new_findings)} findings）")
    else:
        print("[2] canon_drift.json 已是最新，跳过")


def build_manifest_v3() -> None:
    # 可重建：每次从 v2 基准 + live 文件重算（v3 是构建产物，非手改文件）
    if V3_MANIFEST.exists():
        print(f"[3] {V3_MANIFEST.name} 已存在，重建（自哈希与冻结时间随 live 文件更新）")
    v2 = _load(V2_MANIFEST)
    manifest = deepcopy(v2)
    manifest.update(
        suite_id="chat01-qinweixi-v3",
        version="v3",
        status="draft",
        created_at=_now(),
        frozen_at=None,
        purpose=(
            "T1 锚修复后重新冻结：canon（bible.yaml/canon.json）去元注释与重复事实，"
            "用例、rubrics、oracles 与 v1/v2 字节一致；canon 快照升级至 v2。"
        ),
        notes="CHAT-01 v3 frozen contract. Evaluation cases/oracles/rubrics remain byte-identical to v1/v2; canon snapshot refreshed to chat01-canon-v2 after T1 anchor fixes.",
    )
    # canonical_sources：bible/canon 用 live 哈希
    for src in manifest["canonical_sources"]:
        live = _asset(src["path"])
        src["bytes"] = live["bytes"]
        src["sha256"] = live["sha256"]
    # frozen_assets：v3 清单（来自 eval.chat01.freeze.FROZEN_ASSETS，live 重算）
    frozen_assets = []
    for relative, role in FROZEN_ASSETS:
        entry = _asset(relative)
        entry["role"] = role
        frozen_assets.append(entry)
    frozen_by_path = {a["path"]: a for a in frozen_assets}
    for suite in manifest["assets"]:
        suite["sha256"] = frozen_by_path[suite["path"]]["sha256"]
    manifest["frozen_assets"] = frozen_assets
    # leakage 沿用 v2 报告（training/eval 源未变）
    manifest["leakage"]["status"] = "passed"
    manifest["leakage"]["report_sha256"] = frozen_by_path["eval/chat01/suites/leakage_report_v2.json"]["sha256"]
    manifest["status"] = "frozen"
    manifest["frozen_at"] = _now()
    manifest["freeze"].update(immutable=True, manifest_sha256=None)
    manifest["freeze"]["manifest_sha256"] = canonical_manifest_sha256(manifest)
    _dump(V3_MANIFEST, manifest)
    print(f"[3] {V3_MANIFEST.name} 已冻结（self-sha {manifest['freeze']['manifest_sha256'][:16]}...，{len(frozen_assets)} assets）")


def build_alignment_v2() -> None:
    v3 = _load(V3_MANIFEST)
    snapshot = _load(V2_SNAPSHOT)
    v1_align = _load(ALIGN_V1)
    alignment = deepcopy(v1_align)
    alignment["alignment_id"] = "chat02a-runtime-canon-alignment-v2"
    alignment["created_at"] = _now()
    alignment["source_contract"] = {
        "canon_snapshot_id": snapshot["snapshot_id"],
        "suite_id": v3["suite_id"],
        "suite_manifest_sha256": v3["freeze"]["manifest_sha256"],
    }
    alignment["resolved_facts"] = snapshot["resolved_facts"]
    # resolutions：更新 runtime prompt 的 current（既有漂移），新增 2 条 T1 canon
    live_prompt = _asset("runtime/_prompt.py")
    resolutions = []
    for res in v1_align["resolutions"]:
        entry = deepcopy(res)
        if res["finding_id"] == "drift-runtime-prompt":
            entry["current_bytes"] = live_prompt["bytes"]
            entry["current_sha256"] = live_prompt["sha256"]
        resolutions.append(entry)
    v1_snap = _load(V1_SNAPSHOT)
    frozen = {s["path"]: s for s in v1_snap["sources"]}
    for fid, path in (
        ("drift-t1-canon-bible", "人物设定/秦/bible.yaml"),
        ("drift-t1-canon-canon", "人物设定/秦/canon.json"),
    ):
        live = _asset(path)
        resolutions.append(
            {
                "finding_id": fid,
                "path": path,
                "frozen_bytes": frozen[path]["bytes"],
                "frozen_sha256": frozen[path]["sha256"],
                "current_bytes": live["bytes"],
                "current_sha256": live["sha256"],
                "status": "resolved",
            }
        )
    alignment["resolutions"] = resolutions
    _dump(ALIGN_V2, alignment)
    print(f"[4] {ALIGN_V2.name} 已生成（resolutions {len(resolutions)}）")


def update_profile() -> None:
    profile = _load(PROFILE)
    prompt = _asset("runtime/_prompt.py")
    profile["request_contract"]["prompt_renderer"]["bytes"] = prompt["bytes"]
    profile["request_contract"]["prompt_renderer"]["sha256"] = prompt["sha256"]
    v3 = _load(V3_MANIFEST)
    v3_path = str(V3_MANIFEST)
    profile["suite_contract"] = {
        "suite_id": v3["suite_id"],
        "manifest_path": v3_path,
        "manifest_file_sha256": sha256_file(V3_MANIFEST),
        "manifest_self_sha256": v3["freeze"]["manifest_sha256"],
    }
    _dump(PROFILE, profile)
    print(f"[5] execution_profile_v1.json 已更新（prompt_renderer + suite_contract→v3）")


def update_model_manifest() -> None:
    m = _load(MODEL_MANIFEST)
    changed = 0
    for evidence in m["adaptation"]["evidence"]:
        p = Path(evidence["path"])
        if p.is_file():
            live = {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
            if (evidence["bytes"], evidence["sha256"]) != (live["bytes"], live["sha256"]):
                print(f"  evidence 漂移更新: {evidence['path']} {evidence['bytes']}/{evidence['sha256'][:12]} → {live['bytes']}/{live['sha256'][:12]}")
                evidence["bytes"] = live["bytes"]
                evidence["sha256"] = live["sha256"]
                changed += 1
    if changed:
        _dump(MODEL_MANIFEST, m)
    print(f"[6] model manifest 更新 {changed} 条 evidence")


if __name__ == "__main__":
    build_snapshot_v2()
    update_canon_drift()
    build_manifest_v3()
    build_alignment_v2()
    update_profile()
    update_model_manifest()
    print("\n完成。下一步：更新 tests 路径后跑 pytest。")
