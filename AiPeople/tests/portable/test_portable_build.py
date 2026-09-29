from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

import pytest

from runtime._real_assets import LocalRetrievalAssetBundle
from runtime.gemma_nf4_assets import LocalGemmaNF4Package
from tests.real_select_e2e._assets import make_bge_package, make_reranker_package
from tests.real_select_e2e.test_gemma_nf4_assets import make_asset
from tools import build_portable_windows as portable


def _write(path: Path, payload: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_app_plan_is_allowlisted_and_excludes_runtime_and_training_state(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    app_root = repository / "AiPeople"

    _write(app_root / "tools" / "baiweixi_chat_app.py")
    _write(app_root / "tools" / "baiweixi_chat_static" / "index.html")
    _write(app_root / "portable_launcher" / "README.txt", "用户说明".encode())
    _write(app_root / "world" / "not-selected.txt")
    _write(app_root / "世界设定" / "松江府" / "canon.json")
    _write(repository / "人物设定" / "主角" / "canon.json")
    _write(repository / "人物设定" / "白未晞" / "canon.json")

    runtime = app_root / "runtime"
    _write(runtime / "keep.py")
    _write(runtime / ".env")
    _write(runtime / ".env.production")
    _write(runtime / "session.sqlite3")
    _write(runtime / "session.sqlite3-wal")
    _write(runtime / "session.sqlite3-shm")
    _write(runtime / "bytecode.pyc")
    _write(runtime / "optimizer.pt")
    _write(runtime / "__pycache__" / "cached.pyc")
    _write(runtime / "checkpoint-42" / "adapter.bin")
    _write(runtime / "checkpoints" / "adapter.bin")
    _write(runtime / "runs" / "events.out")

    # Top-level training/evaluation trees are not part of APP_SINGLE_FILES or APP_TREES.
    _write(app_root / "training" / "training-only.bin")
    _write(app_root / "eval" / "interactive.sqlite3")
    _write(app_root / ".env")

    builder = portable.PlanBuilder()
    portable._add_app_files(builder, app_root)
    destinations = {item.destination.as_posix() for item in builder.files}

    assert {
        "app/tools/baiweixi_chat_app.py",
        "app/tools/baiweixi_chat_static/index.html",
        "app/runtime/keep.py",
        "app/世界设定/松江府/canon.json",
        "app/人物设定/主角/canon.json",
        "app/人物设定/白未晞/canon.json",
        "README.txt",
    } <= destinations
    assert not any("training-only.bin" in item for item in destinations)
    assert not any("not-selected.txt" in item for item in destinations)
    assert not any(
        item.endswith(
            (
                "/.env",
                "/.env.production",
                ".sqlite3",
                ".sqlite3-wal",
                ".sqlite3-shm",
                ".pyc",
                "/optimizer.pt",
                "/adapter.bin",
                "/events.out",
            )
        )
        for item in destinations
    )
    assert builder.exclusions == {
        "directory:__pycache__": 1,
        "directory:checkpoint": 1,
        "directory:checkpoints": 1,
        "directory:runs": 1,
        "file:environment": 2,
        "file:runtime-state-or-cache": 4,
        "file:training-state": 1,
    }


def _tiny_build_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Path], portable.BuildPlan]:
    inputs = tmp_path / "tiny inputs"
    assets = inputs / "assets"
    python_root = inputs / "python"
    app_root = inputs / "app"
    launcher = inputs / "launcher" / "AiPeople.exe"
    for directory in (assets, python_root, app_root):
        directory.mkdir(parents=True)

    sources = (
        (_write(launcher, b"MZ-launcher"), "AiPeople.exe", "launcher"),
        (
            _write(inputs / "payload" / "python.exe", b"python-runtime"),
            "runtime/python.exe",
            "python-stdlib",
        ),
        (
            _write(inputs / "payload" / "baiweixi_chat_app.py", b"print('app')\n"),
            "app/tools/baiweixi_chat_app.py",
            "app",
        ),
        (
            _write(inputs / "payload" / "portable_assets.json", b"{}\n"),
            "assets/portable_assets.json",
            "assets",
        ),
        (
            _write(
                inputs / "payload" / "README.txt",
                (portable.APP_ROOT / portable.USER_README_SOURCE).read_bytes(),
            ),
            "README.txt",
            "documentation",
        ),
    )
    files = [
        portable.PlannedFile(
            source=source.resolve(),
            destination=PurePosixPath(destination),
            category=category,
            size=source.stat().st_size,
        )
        for source, destination, category in sources
    ]
    plan = portable.BuildPlan(
        files=files,
        distributions=[],
        exclusions={"file:environment": 1},
        warnings=["tiny fixture warning"],
        licenses=[],
        missing_licenses=["Adapter:LICENSE"],
    )
    layout = portable.PythonLayout(
        environment_root=python_root,
        base_root=python_root,
        executable=python_root / "python.exe",
        package_roots=(),
        version="3.11.9",
    )
    monkeypatch.setattr(
        portable,
        "create_build_plan",
        lambda **_kwargs: (plan, layout),
    )
    return {
        "assets": assets,
        "python_root": python_root,
        "app_root": app_root,
        "launcher": launcher,
    }, plan


def test_tiny_build_writes_layout_manifest_hashes_and_exact_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, plan = _tiny_build_context(tmp_path, monkeypatch)
    output = tmp_path / "发布 目录" / "AiPeoplePortable"

    summary = portable.build_portable(output=output, **inputs)

    assert summary["status"] == "built"
    assert {item.name for item in output.iterdir()} == {
        "AiPeople.exe",
        "app",
        "assets",
        "data",
        "licenses",
        "logs",
        "README.txt",
        "release_manifest.json",
        "runtime",
    }
    manifest_path = output / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_paths = {item.destination.as_posix() for item in plan.files}
    assert manifest["format"] == "aipeople-windows-portable-directory"
    assert manifest["directories"] == [
        "app",
        "assets",
        "data",
        "licenses",
        "logs",
        "runtime",
    ]
    assert manifest["python"] == {
        "version": "3.11.9",
        "executable": "runtime/python.exe",
        "distributions": [],
    }
    assert {item["path"] for item in manifest["files"]} == expected_paths
    assert (
        manifest["payload_file_count"]
        == summary["payload_file_count"]
        == len(plan.files)
    )
    assert (
        manifest["payload_total_bytes"]
        == summary["payload_total_bytes"]
        == sum(item.size for item in plan.files)
    )
    assert manifest["payload_sha256"] == summary["payload_sha256"]
    assert manifest["missing_licenses"] == ["Adapter:LICENSE"]
    assert manifest["exclusions"] == {"file:environment": 1}
    readme = (output / "README.txt").read_text(encoding="utf-8")
    for expected in (
        "完整保留解压后的整个目录",
        "双击 AiPeople.exe",
        "首次启动",
        "Windows x64",
        "NVIDIA GPU",
        "至少 12 GB 显存",
        "无需安装 Python 或 CUDA Toolkit",
        "Program Files",
        "data 目录",
        "logs 目录",
        "托盘菜单并选择 Exit",
        "完全离线",
        "未知发布者",
        "SHA256 哈希核验",
    ):
        assert expected in readme
    readme_entry = next(
        item for item in manifest["files"] if item["path"] == "README.txt"
    )
    assert readme_entry["category"] == "documentation"
    assert readme_entry["sha256"] == _sha256(output / "README.txt")
    for entry in manifest["files"]:
        delivered = output.joinpath(*PurePosixPath(entry["path"]).parts)
        assert delivered.stat().st_size == entry["bytes"]
        assert _sha256(delivered) == entry["sha256"]

    assert summary["release_manifest_bytes"] == manifest_path.stat().st_size
    assert summary["distribution_total_bytes"] == sum(
        path.stat().st_size for path in output.rglob("*") if path.is_file()
    )


def test_existing_output_is_preserved_until_replace_build_fully_stages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs, _plan = _tiny_build_context(tmp_path, monkeypatch)
    output = tmp_path / "existing output"
    sentinel = _write(output / "keep-me.txt", b"old release")

    with pytest.raises(portable.PortableBuildError, match="output already exists"):
        portable.build_portable(output=output, **inputs)
    assert sentinel.read_bytes() == b"old release"

    original_copy = portable._copy_and_hash
    calls = 0

    def fail_during_staging(source: Path, destination: Path) -> str:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated staging failure")
        return original_copy(source, destination)

    monkeypatch.setattr(portable, "_copy_and_hash", fail_during_staging)
    with pytest.raises(OSError, match="simulated staging failure"):
        portable.build_portable(output=output, replace=True, **inputs)
    assert sentinel.read_bytes() == b"old release"
    assert not list(output.parent.glob(f".{output.name}.staging-*"))
    assert not list(output.parent.glob(f".{output.name}.backup-*"))

    monkeypatch.setattr(portable, "_copy_and_hash", original_copy)
    summary = portable.build_portable(output=output, replace=True, **inputs)
    assert summary["status"] == "built"
    assert not sentinel.exists()
    assert (output / "release_manifest.json").is_file()
    assert not list(output.parent.glob(f".{output.name}.backup-*"))


def test_relative_assets_survive_chinese_space_move_and_unrelated_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "原始 便携包"
    assets = source / "assets"
    assets.mkdir(parents=True)
    gemma_manifest = make_asset(assets, relative_paths=True)
    expected_artifact = json.loads(gemma_manifest.read_text(encoding="utf-8"))[
        "artifact_sha256"
    ]

    retrieval = assets / "retrieval"
    bge_root = make_bge_package(retrieval / "bge-small-zh-v1.5")
    reranker_root = make_reranker_package(retrieval / "qwen3-reranker-0.6b")
    bge_manifest = json.loads(
        (bge_root / "bge_manifest.json").read_text(encoding="utf-8")
    )
    reranker_manifest = json.loads(
        (reranker_root / "reranker_manifest.json").read_text(encoding="utf-8")
    )
    retrieval_manifest = retrieval / "retrieval_assets.json"
    retrieval_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "network_access_at_runtime": "forbidden",
                "bge": {
                    "path": "bge-small-zh-v1.5",
                    "revision": bge_manifest["revision"],
                    "artifact_sha256": bge_manifest["artifact_sha256"],
                },
                "reranker": {
                    "path": "qwen3-reranker-0.6b",
                    "revision": reranker_manifest["revision"],
                    "artifact_sha256": reranker_manifest["artifact_sha256"],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (assets / "portable_assets.json").write_text(
        json.dumps(
            {
                "format": "portable-bitsandbytes-nf4-base-plus-separate-lora",
                "model_manifest": gemma_manifest.name,
            }
        ),
        encoding="utf-8",
    )

    moved_parent = tmp_path / "二次 位置"
    moved_parent.mkdir()
    moved = moved_parent / "白未晞 便携包"
    source.rename(moved)
    unrelated = tmp_path / "无关 工作目录"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    moved_assets = moved / "assets"
    roots = portable.validate_assets(moved_assets)
    gemma = LocalGemmaNF4Package.load(moved_assets / "gemma4_nf4_runtime_manifest.json")
    retrieval_bundle = LocalRetrievalAssetBundle.load(
        moved_assets / "retrieval" / "retrieval_assets.json"
    )

    assert not source.exists()
    assert roots["gemma"] == gemma.model_root == (moved_assets / "model").resolve()
    assert (
        roots["adapter"] == gemma.adapter_root == (moved_assets / "adapter").resolve()
    )
    assert gemma.identity.artifact_sha256 == expected_artifact
    assert (
        roots["bge"]
        == retrieval_bundle.bge.root
        == (moved_assets / "retrieval" / "bge-small-zh-v1.5").resolve()
    )
    assert (
        roots["qwen"]
        == retrieval_bundle.reranker.root
        == (moved_assets / "retrieval" / "qwen3-reranker-0.6b").resolve()
    )
