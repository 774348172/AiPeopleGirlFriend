"""PackageResolver：package ref 解析、校验与依赖闭合（《数据生成器v4设计》§5、§5.5）。

- ref 语法 `pkg:<package_id>@<package_version>`。
- 校验：package 存在、`status=approved`、`content_hash` 自洽（sha256 规范 JSON，排除
  content_hash 字段本身）、`compatible_core_range` 匹配当前核心版本、依赖闭合无环。
- 产出 lock 的 packages pin（package_version/content_hash/status），供 lock 一致性校验。

阶段 1 只支持最小 semver range 子集：`x.y.z`（精确）、`*`、
`>=x.y.z` / `>x.y.z` / `<=x.y.z` / `<x.y.z` 及逗号组合（如 `>=1.0.0,<2.0.0`）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from .errors import PackageIncompatibleError, PackageInvalidError
from .interfaces import PackageRegistry
from .plan import PackageSetV4

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def canonical_json(obj: dict[str, Any]) -> str:
    """规范 JSON：排序键、紧凑分隔、不转义非 ASCII。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash_of(package: dict[str, Any]) -> str:
    """package content_hash 计算：排除 content_hash 字段本身，避免自指。"""
    body = {k: v for k, v in package.items() if k != "content_hash"}
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


# ───────────────────────── 最小 semver range ─────────────────────────

def _parse_version(text: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.match(text.strip())
    if not match:
        raise ValueError(f"非法版本号: {text!r}")
    return tuple(int(part) for part in match.groups())


def _cmp(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return (a > b) - (a < b)


def _match_single(version: tuple[int, int, int], constraint: str) -> bool:
    constraint = constraint.strip()
    if constraint == "*":
        return True
    for op in (">=", "<=", ">", "<", "="):
        if constraint.startswith(op):
            bound = _parse_version(constraint[len(op) :])
            c = _cmp(version, bound)
            if op == ">=":
                return c >= 0
            if op == "<=":
                return c <= 0
            if op == ">":
                return c > 0
            if op == "<":
                return c < 0
            return c == 0
    return _cmp(version, _parse_version(constraint)) == 0


def version_matches(version: str, range_spec: str) -> bool:
    """version 是否满足 range_spec（逗号分隔的合取）。"""
    v = _parse_version(version)
    return all(_match_single(v, part) for part in range_spec.split(","))


# ───────────────────────── 解析结果 ─────────────────────────

@dataclass(frozen=True, slots=True)
class ResolvedPackages:
    profile: dict[str, Any]
    protocol: dict[str, Any]
    recipe: dict[str, Any]
    release: dict[str, Any]
    dependencies: dict[str, Any] = field(default_factory=dict)

    @property
    def all_packages(self) -> dict[str, Any]:
        return {
            self.profile["package_id"]: self.profile,
            self.protocol["package_id"]: self.protocol,
            self.recipe["package_id"]: self.recipe,
            self.release["package_id"]: self.release,
            **self.dependencies,
        }

    def lock_packages(self) -> dict[str, dict[str, str]]:
        """lock_v4.schema.json 的 packages 部分（只含 approved）。"""
        return {
            pid: {
                "package_version": pkg["package_version"],
                "content_hash": pkg["content_hash"],
                "status": pkg["status"],
            }
            for pid, pkg in self.all_packages.items()
        }


class PackageResolver:
    """从 registry 解析 PackageSetV4 的四类 package 及其依赖。"""

    def __init__(self, registry: PackageRegistry, core_version: str = __version__) -> None:
        self._registry = registry
        self._core_version = core_version

    def resolve(self, package_set: PackageSetV4) -> ResolvedPackages:
        refs = [
            ("profile", package_set.profile_package_ref),
            ("protocol", package_set.protocol_bundle_ref),
            ("recipe", package_set.dataset_recipe_ref),
            ("release", package_set.release_policy_ref),
        ]
        top: dict[str, dict[str, Any]] = {}
        for kind, ref in refs:
            top[kind] = self._resolve_ref(ref)
        # 递归依赖闭合（含环检测）
        deps: dict[str, dict[str, Any]] = {}
        for pkg in top.values():
            self._collect_dependencies(pkg, deps, visiting=set(), visited=set())
        for pid in top.values():
            deps.pop(pid["package_id"], None)
        return ResolvedPackages(
            profile=top["profile"],
            protocol=top["protocol"],
            recipe=top["recipe"],
            release=top["release"],
            dependencies=deps,
        )

    def _resolve_ref(self, ref: str) -> dict[str, Any]:
        match = re.match(r"^pkg:([a-z0-9][a-z0-9._-]*)@([0-9]+\.[0-9]+\.[0-9]+)$", ref)
        if not match:
            raise PackageInvalidError(f"非法 package ref: {ref!r}")
        package_id, version = match.groups()
        package = self._registry.get(package_id)
        if package is None:
            raise PackageInvalidError(f"package 不存在: {package_id}")
        if package.get("package_version") != version:
            raise PackageIncompatibleError(
                f"{package_id} 引用版本 {version}，注册表版本 {package.get('package_version')}"
            )
        self._validate_package(package)
        return package

    def _validate_package(self, package: dict[str, Any]) -> None:
        package_id = package.get("package_id")
        if package.get("status") != "approved":
            raise PackageInvalidError(f"{package_id} status 不是 approved: {package.get('status')!r}")
        declared = package.get("content_hash")
        if declared != content_hash_of(package):
            raise PackageInvalidError(
                f"{package_id} content_hash 不自洽: 声明 {declared}，计算 {content_hash_of(package)}"
            )
        range_spec = package.get("compatible_core_range", "*")
        if not version_matches(self._core_version, range_spec):
            raise PackageIncompatibleError(
                f"{package_id} 要求 core {range_spec}，当前 {self._core_version}"
            )

    def _collect_dependencies(
        self,
        package: dict[str, Any],
        deps: dict[str, dict[str, Any]],
        *,
        visiting: set[str],
        visited: set[str],
    ) -> None:
        package_id = package["package_id"]
        if package_id in visited:
            return
        if package_id in visiting:
            raise PackageInvalidError(f"package 依赖成环: {package_id}")
        visiting.add(package_id)
        for dep_ref in package.get("dependencies", []):
            dep = self._resolve_ref(dep_ref)
            self._collect_dependencies(dep, deps, visiting=visiting, visited=visited)
            deps[dep["package_id"]] = dep
        visiting.discard(package_id)
        visited.add(package_id)
