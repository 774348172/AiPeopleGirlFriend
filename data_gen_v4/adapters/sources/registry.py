"""文件系统 package 基础设施（阶段 3）。

- FilePackageRegistry：按 profiles/<profile_id>/manifest.yaml 的引用加载
  四类 package 文件为 package dict（content_hash 自洽校验）。
- CompositeSourceLoader：按 source ref 后缀路由到 StructuredFile /
  MarkdownCanon adapter，对外暴露统一 SourceLoader 接口。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from data_gen_v4.adapters.sources.markdown_canon import MarkdownCanonSourceAdapter
from data_gen_v4.adapters.sources.structured_file import StructuredFileSourceAdapter
from data_gen_v4.core.errors import PackageInvalidError, SourceSnapshotFailedError
from data_gen_v4.core.resolver import canonical_json, content_hash_of

_PACKAGE_TYPES = {"profile", "protocol", "recipe", "release"}


def _load_doc(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in (".yaml", ".yml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif path.suffix.lower() == ".json":
        doc = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise PackageInvalidError(f"不支持的 package 文件类型: {path.suffix}")
    if not isinstance(doc, dict):
        raise PackageInvalidError(f"package 文件顶层必须是对象: {path}")
    return doc


class FilePackageRegistry:
    """从 profiles 目录加载四类 package；可附加独立 package 文件目录
    （如 data_gen_v4/packages/protocols/ 下的默认协议包）。

    manifest.yaml 约定（阶段 3 冻结）：
        profile_id: <id>
        packages:
          profile: profile.yaml
          protocol: protocol.yaml
          recipe: recipe.yaml
          release: release.yaml
    package 文件里的 package_id 由内容决定（不要求等于目录名）。
    """

    def __init__(self, profiles_root: Path, extra_package_files: list[Path] | None = None) -> None:
        self._root = Path(profiles_root)
        self._extra_cache: dict[str, dict[str, Any]] = {}
        for path in (extra_package_files or []):
            package = _load_doc(Path(path))
            if package.get("package_type") not in _PACKAGE_TYPES:
                continue
            self._validate(package, Path(path))
            self._extra_cache[package["package_id"]] = package

    def get(self, package_id: str) -> dict[str, Any] | None:
        if package_id in self._extra_cache:
            return self._extra_cache[package_id]
        package_dir = self._find_package_dir(package_id)
        if package_dir is None:
            return None
        manifest = _load_doc(package_dir / "manifest.yaml")
        refs = manifest.get("packages", {})
        if not isinstance(refs, dict):
            raise PackageInvalidError(f"{package_dir}/manifest.yaml 缺少 packages 引用")
        for kind in _PACKAGE_TYPES:
            filename = refs.get(kind)
            if filename:
                package = _load_doc(package_dir / str(filename))
                if package.get("package_id") == package_id:
                    self._validate(package, package_dir / str(filename))
                    return package
        return None

    def list_profiles(self) -> list[str]:
        return sorted(
            directory.name
            for directory in self._root.iterdir()
            if directory.is_dir() and (directory / "manifest.yaml").exists()
        )

    def _find_package_dir(self, package_id: str) -> Path | None:
        for directory in self._root.iterdir():
            if not directory.is_dir():
                continue
            manifest = directory / "manifest.yaml"
            if not manifest.exists():
                continue
            try:
                doc = _load_doc(manifest)
            except Exception:  # noqa: BLE001
                continue
            refs = doc.get("packages", {})
            for filename in refs.values():
                if not filename:
                    continue
                package = _load_doc(directory / str(filename))
                if package.get("package_id") == package_id:
                    return directory
        return None

    @staticmethod
    def _validate(package: dict[str, Any], path: Path) -> None:
        """content_hash 自动注入：文件内可省略；若声明则必须与内容自洽。"""
        computed = content_hash_of(package)
        declared = package.get("content_hash")
        if declared is not None and declared != computed:
            raise PackageInvalidError(f"{path} content_hash 不自洽")
        package["content_hash"] = computed


class CompositeSourceLoader:
    """按 source ref 路由到具体 SourceAdapter（.md → MarkdownCanon；其余 → StructuredFile）。"""

    def __init__(self, sources_root: Path) -> None:
        self._sources_root = Path(sources_root)
        self._structured = StructuredFileSourceAdapter(self._sources_root)
        self._markdown = MarkdownCanonSourceAdapter(self._sources_root)

    def load_snapshot(self, source_ref: str, snapshot_policy: str = "freeze") -> dict[str, Any]:
        if not source_ref.startswith("file:"):
            raise SourceSnapshotFailedError(f"不支持的 source ref: {source_ref!r}")
        suffix = Path(source_ref[len("file:") :]).suffix.lower()
        if suffix == ".md":
            return self._markdown.load_snapshot(source_ref, snapshot_policy)
        if suffix in (".yaml", ".yml", ".json"):
            return self._structured.load_snapshot(source_ref, snapshot_policy)
        raise SourceSnapshotFailedError(f"无适配的来源类型: {source_ref}")
