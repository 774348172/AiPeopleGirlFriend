"""多角色接线：gen_v4 通用入口的角色包发现与输出命名（2026-08-08）。

覆盖：
- build_package_set：从 profiles/<id>/manifest.yaml 推导四类包 ref（无硬编码）；
  包文件缺失/缺 package_id → 显式失败；
- load_manifest：未知 profile → SystemExit（明确报错，不回退）；
- run_artifacts：多角色输出命名（{profile_id}_v4_{count}.*）与 run_id 前缀；
- 真实 baiweixi 包（当前 P0 女主角）可被 build_package_set 发现。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gen_v4 import (  # noqa: E402
    ROOT as GEN_ROOT,
    build_package_set,
    load_manifest,
    run_artifacts,
)
from gen_qin_v4 import QWX_PACKAGE_SET  # noqa: E402

PROFILES = GEN_ROOT / "profiles"


@pytest.fixture()
def fake_profile_dir(tmp_path: Path):
    """临时假角色包（只含 build_package_set 需要的 package_id/package_version）。

    使用与真实包不冲突的 id（test_fixture_role），避免覆盖真实 baiweixi 包。
    """
    pkg_dir = tmp_path / "profiles" / "test_fixture_role"
    pkg_dir.mkdir(parents=True)
    protocol_abs = GEN_ROOT / "data_gen_v4" / "packages" / "protocols" / "relationship-runtime-v1.yaml"
    (pkg_dir / "manifest.yaml").write_text(
        "profile_id: test_fixture_role\n"
        f"packages:\n"
        f"  profile: profile.yaml\n"
        f"  protocol: {protocol_abs.as_posix()}\n"
        f"  recipe: recipe.yaml\n"
        f"  release: release.yaml\n",
        encoding="utf-8",
    )
    (pkg_dir / "profile.yaml").write_text(
        "package_id: profile.test_fixture_role\npackage_type: profile\npackage_version: 0.1.0\n",
        encoding="utf-8",
    )
    (pkg_dir / "recipe.yaml").write_text(
        "package_id: recipe.test_fixture_role.v1\npackage_type: recipe\npackage_version: 0.1.0\n",
        encoding="utf-8",
    )
    (pkg_dir / "release.yaml").write_text(
        "package_id: release.test_fixture_role.v1\npackage_type: release\npackage_version: 0.1.0\n",
        encoding="utf-8",
    )
    return tmp_path / "profiles"


def test_build_package_set_discovers_manifest_refs(fake_profile_dir):
    ps = build_package_set(fake_profile_dir, "test_fixture_role")
    assert ps.profile_package_ref == "pkg:profile.test_fixture_role@0.1.0"
    assert ps.dataset_recipe_ref == "pkg:recipe.test_fixture_role.v1@0.1.0"
    assert ps.release_policy_ref == "pkg:release.test_fixture_role.v1@0.1.0"
    # 共享协议包（跨角色）：ref 与秦一致
    assert ps.protocol_bundle_ref == QWX_PACKAGE_SET.protocol_bundle_ref


def test_build_package_set_baiweixi_current_p0():
    # 当前 P0 女主角包可被发现（manifest 齐全）
    ps = build_package_set(PROFILES, "baiweixi")
    assert ps.profile_package_ref == "pkg:profile.baiweixi@0.2.0"
    assert ps.dataset_recipe_ref == "pkg:recipe.baiweixi.v1@0.2.0"
    assert ps.release_policy_ref == "pkg:release.baiweixi.v1@0.2.0"
    assert ps.protocol_bundle_ref == QWX_PACKAGE_SET.protocol_bundle_ref


def test_build_package_set_qinweixi_historical_still_resolvable():
    # 历史角色包（秦）经修复后仍可解析（指向历史正典），引用不变
    ps = build_package_set(PROFILES, "qinweixi")
    assert ps == QWX_PACKAGE_SET


def test_load_manifest_unknown_profile_fails(tmp_path):
    with pytest.raises(SystemExit, match="未知 profile"):
        load_manifest(tmp_path / "profiles", "not_exist")


def test_build_package_set_missing_package_file_fails(fake_profile_dir):
    (fake_profile_dir / "test_fixture_role" / "recipe.yaml").unlink()
    with pytest.raises(SystemExit, match="recipe"):
        build_package_set(fake_profile_dir, "test_fixture_role")


def test_run_artifacts_profile_named_outputs():
    run_id, sink, out = run_artifacts("baiweixi", 20, GEN_ROOT / "训练数据")
    assert run_id.startswith("baiweixi-real-20-")
    assert sink.name == "baiweixi_v4_20.sqlite"
    assert out.name == "baiweixi_v4_20.jsonl"
    # out_override 时 jsonl 与 sqlite 都跟随覆盖（2026-08-09：ledger 与 --out 同名，
    # 避免多次运行按 count 命名互相覆盖，G7 apply_review 写错 ledger）
    _, sink2, out2 = run_artifacts("baiweixi", 20, GEN_ROOT / "训练数据",
                                   out_override="训练数据/custom.jsonl")
    assert sink2 == GEN_ROOT / "训练数据/custom.sqlite"
    assert out2 == GEN_ROOT / "训练数据/custom.jsonl"
