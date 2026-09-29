from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = APP_ROOT.parent
DEFAULT_ASSETS = REPOSITORY_ROOT / "release" / "build" / "portable-assets"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "release" / "build" / "AiPeoplePortable"
USER_README_SOURCE = "portable_launcher/README.txt"

# These are the direct imports used by the production chat process. Metadata
# dependencies are followed recursively from this small, auditable seed set.
DEFAULT_RUNTIME_PACKAGES = (
    "accelerate",
    "bitsandbytes",
    "fastapi",
    "httpx",
    "numpy",
    "peft",
    "pydantic",
    "safetensors",
    "tokenizers",
    "torch",
    "torchvision",
    "transformers",
    "tzdata",
    "unsloth",
    "unsloth-zoo",
    "uvicorn",
)

REQUIRED_ASSET_FILES = (
    "portable_assets.json",
    "gemma4_nf4_runtime_manifest.json",
    "retrieval/retrieval_assets.json",
)
APP_SINGLE_FILES = ("tools/baiweixi_chat_app.py",)
APP_TREES = (
    ("runtime", "runtime"),
    ("tools/baiweixi_chat_static", "tools/baiweixi_chat_static"),
    ("世界设定/松江府", "世界设定/松江府"),
)

COMMON_EXCLUDED_DIRS = frozenset(
    {
        ".cache",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        "__pycache__",
        "checkpoints",
        "logs",
        "runs",
        "wandb",
    }
)
PACKAGE_EXCLUDED_DIRS = frozenset({"test", "tests"})
UNSLOTH_NON_RUNTIME_TOP_LEVEL = frozenset({"studio", "unsloth_cli"})
EXCLUDED_SUFFIXES = (
    ".bak",
    ".db",
    ".db-shm",
    ".db-wal",
    ".log",
    ".orig",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".tmp",
)
TRAINING_FILE_NAMES = frozenset(
    {
        "optimizer.pt",
        "rng_state.pth",
        "scheduler.pt",
        "trainer_state.json",
        "training_args.bin",
    }
)
SENSITIVE_FILE_NAMES = frozenset(
    {
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "service-account.json",
    }
)
SENSITIVE_SUFFIXES = frozenset({".key", ".p12", ".pfx"})
SECRET_SIGNATURES = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("google-api-key", re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b")),
)
ABSOLUTE_WINDOWS_PATH = re.compile(rb"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]")
TEXT_SCAN_SUFFIXES = frozenset(
    {
        ".cfg",
        ".conf",
        ".css",
        ".html",
        ".ini",
        ".jinja",
        ".js",
        ".json",
        ".md",
        ".py",
        ".sql",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
MAX_SECRET_SCAN_BYTES = 4 * 1024 * 1024
COPY_CHUNK_BYTES = 16 * 1024 * 1024
DISK_HEADROOM_BYTES = 512 * 1024 * 1024


class PortableBuildError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PythonLayout:
    environment_root: Path
    base_root: Path
    executable: Path
    package_roots: tuple[Path, ...]
    version: str

    def destination_for(self, source: Path) -> PurePosixPath | None:
        for root in _unique_paths((self.environment_root, self.base_root)):
            relative = _relative_to(source, root)
            if relative is not None:
                return PurePosixPath(*relative.parts)
        return None


@dataclass(frozen=True, slots=True)
class SelectedDistribution:
    name: str
    version: str
    distribution: importlib.metadata.Distribution


@dataclass(frozen=True, slots=True)
class PlannedFile:
    source: Path
    destination: PurePosixPath
    category: str
    size: int


@dataclass(frozen=True, slots=True)
class LicenseRecord:
    component: str
    kind: str
    destination: PurePosixPath
    source_label: str


@dataclass(slots=True)
class BuildPlan:
    files: list[PlannedFile]
    distributions: list[SelectedDistribution]
    exclusions: dict[str, int]
    warnings: list[str]
    licenses: list[LicenseRecord]
    missing_licenses: list[str]

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


class PlanBuilder:
    def __init__(self) -> None:
        self._destinations: dict[str, PlannedFile] = {}
        self._exclusions: dict[str, int] = {}

    @property
    def exclusions(self) -> dict[str, int]:
        return dict(sorted(self._exclusions.items()))

    @property
    def files(self) -> list[PlannedFile]:
        return sorted(
            self._destinations.values(),
            key=lambda item: (str(item.destination).casefold(), str(item.destination)),
        )

    def exclude(self, reason: str) -> None:
        self._exclusions[reason] = self._exclusions.get(reason, 0) + 1

    def add_file(
        self,
        source: Path,
        destination: str | PurePosixPath,
        category: str,
        *,
        scan_secrets: bool = False,
    ) -> None:
        source = source.resolve()
        destination = _safe_relative(destination)
        if not _is_regular_file(source):
            raise PortableBuildError(f"required regular file is missing: {source}")
        reason = _excluded_file_reason(destination, category)
        if reason is not None:
            self.exclude(reason)
            return
        if scan_secrets:
            _assert_no_secret(source, destination)
        item = PlannedFile(
            source=source,
            destination=destination,
            category=category,
            size=_lstat(source).st_size,
        )
        key = str(destination).replace("/", "\\").casefold()
        previous = self._destinations.get(key)
        if previous is not None:
            if previous.source == source and previous.destination == destination:
                return
            raise PortableBuildError(
                "case-insensitive destination collision: "
                f"{previous.destination} and {destination}"
            )
        self._destinations[key] = item

    def add_tree(
        self,
        source_root: Path,
        destination_root: str | PurePosixPath,
        category: str,
        *,
        scan_secrets: bool = False,
    ) -> None:
        source_root = source_root.resolve()
        if not source_root.is_dir():
            raise PortableBuildError(f"required directory is missing: {source_root}")
        destination_root = _safe_relative(destination_root)
        for source, relative in _walk_files(source_root, category, self._exclusions):
            self.add_file(
                source,
                destination_root / PurePosixPath(*relative.parts),
                category,
                scan_secrets=scan_secrets,
            )


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    keys: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        key = os.path.normcase(str(resolved))
        if key not in keys:
            keys.add(key)
            result.append(resolved)
    return tuple(result)


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _is_within(path: Path, root: Path) -> bool:
    return path == root or _relative_to(path, root) is not None


def _safe_relative(value: str | PurePosixPath) -> PurePosixPath:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PortableBuildError(f"unsafe relative destination: {value}")
    if any(":" in part or "\x00" in part for part in path.parts):
        raise PortableBuildError(f"unsafe relative destination: {value}")
    return path


def _long_path(path: Path) -> str:
    value = os.path.normpath(os.path.abspath(str(path)))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _lstat(path: Path) -> os.stat_result:
    return os.stat(_long_path(path), follow_symlinks=False)


def _is_reparse(path: Path) -> bool:
    info = _lstat(path)
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag) or path.is_symlink()


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(_lstat(path).st_mode) and not _is_reparse(path)
    except (FileNotFoundError, OSError):
        return False


def _excluded_directory_reason(relative: Path, category: str) -> str | None:
    name = relative.name.casefold()
    if name in COMMON_EXCLUDED_DIRS:
        return f"directory:{name}"
    if name.startswith("checkpoint-"):
        return "directory:checkpoint"
    if (
        category in {"python-package", "python-stdlib"}
        and name in PACKAGE_EXCLUDED_DIRS
    ):
        return f"directory:{name}"
    # Lib/site-packages/studio and unsloth_cli are bundled training/CLI
    # products, not imports used by the chat backend.
    if (
        category == "python-package"
        and len(relative.parts) >= 3
        and relative.parts[0].casefold() == "lib"
        and relative.parts[1].casefold() == "site-packages"
        and relative.parts[2].casefold() in UNSLOTH_NON_RUNTIME_TOP_LEVEL
    ):
        return f"directory:{relative.parts[2].casefold()}"
    return None


def _excluded_file_reason(relative: PurePosixPath, category: str) -> str | None:
    name = relative.name.casefold()
    if name == ".env" or name.startswith(".env."):
        return "file:environment"
    if name in TRAINING_FILE_NAMES:
        return "file:training-state"
    if category == "python-package" and name == "direct_url.json":
        return "python-package:direct-url"
    if any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return "file:runtime-state-or-cache"
    if category in {"app", "assets", "license", "launcher"} and (
        name in SENSITIVE_FILE_NAMES or PurePosixPath(name).suffix in SENSITIVE_SUFFIXES
    ):
        return "file:sensitive-name"
    return None


def _walk_files(
    root: Path,
    category: str,
    exclusions: dict[str, int],
) -> Iterable[tuple[Path, Path]]:
    stack: list[Path] = [Path()]
    while stack:
        relative_dir = stack.pop()
        logical_dir = root / relative_dir
        try:
            entries = sorted(
                os.scandir(_long_path(logical_dir)),
                key=lambda entry: (entry.name.casefold(), entry.name),
                reverse=True,
            )
        except OSError as error:
            raise PortableBuildError(
                f"cannot enumerate directory: {logical_dir}"
            ) from error
        pending_dirs: list[Path] = []
        for entry in entries:
            relative = relative_dir / entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise PortableBuildError(
                    f"cannot inspect source entry: {root / relative}"
                ) from error
            attributes = getattr(info, "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if entry.is_symlink() or attributes & reparse_flag:
                raise PortableBuildError(
                    f"source tree contains a link/reparse point: {root / relative}"
                )
            if stat.S_ISDIR(info.st_mode):
                reason = _excluded_directory_reason(relative, category)
                if reason is not None:
                    exclusions[reason] = exclusions.get(reason, 0) + 1
                else:
                    pending_dirs.append(relative)
            elif stat.S_ISREG(info.st_mode):
                destination = PurePosixPath(*relative.parts)
                reason = _excluded_file_reason(destination, category)
                if reason is not None:
                    exclusions[reason] = exclusions.get(reason, 0) + 1
                else:
                    yield root / relative, relative
            else:
                raise PortableBuildError(f"unsupported source entry: {root / relative}")
        stack.extend(pending_dirs)


def _assert_no_secret(source: Path, destination: PurePosixPath) -> None:
    suffix = source.suffix.casefold()
    if (
        suffix not in TEXT_SCAN_SUFFIXES
        or _lstat(source).st_size > MAX_SECRET_SCAN_BYTES
    ):
        return
    try:
        with open(_long_path(source), "rb") as stream:
            payload = stream.read()
    except OSError as error:
        raise PortableBuildError(f"cannot scan source file: {source}") from error
    for label, pattern in SECRET_SIGNATURES:
        if pattern.search(payload):
            # Never include the matched bytes in an error message.
            raise PortableBuildError(
                f"possible {label} detected in release input: {destination}"
            )
    if ABSOLUTE_WINDOWS_PATH.search(payload):
        raise PortableBuildError(
            f"absolute Windows path detected in release input: {destination}"
        )


def _parse_pyvenv_home(path: Path) -> Path | None:
    config = path / "pyvenv.cfg"
    if not config.is_file():
        return None
    try:
        lines = config.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise PortableBuildError(
            f"cannot read Python environment config: {config}"
        ) from error
    for line in lines:
        key, separator, value = line.partition("=")
        if separator and key.strip().casefold() == "home":
            home = Path(value.strip()).expanduser().resolve()
            if not home.is_dir():
                raise PortableBuildError(
                    f"Python base directory does not exist: {home}"
                )
            return home
    raise PortableBuildError(f"Python environment config has no home entry: {config}")


def inspect_python_layout(python_root: Path) -> PythonLayout:
    environment_root = python_root.expanduser().resolve()
    if not environment_root.is_dir():
        raise PortableBuildError(f"Python root is not a directory: {environment_root}")
    venv_home = _parse_pyvenv_home(environment_root)
    base_root = (venv_home or environment_root).resolve()
    executable_candidates = (
        environment_root / "python.exe",
        environment_root / "Scripts" / "python.exe",
    )
    executable = next((item for item in executable_candidates if item.is_file()), None)
    if executable is None:
        raise PortableBuildError(
            f"Python executable is missing below: {environment_root}"
        )
    base_executable = base_root / "python.exe"
    if not base_executable.is_file():
        raise PortableBuildError(
            f"base Python executable is missing: {base_executable}"
        )
    package_roots = [environment_root / "Lib" / "site-packages"]
    if venv_home is not None:
        config_text = (environment_root / "pyvenv.cfg").read_text(encoding="utf-8-sig")
        if re.search(
            r"(?im)^\s*include-system-site-packages\s*=\s*true\s*$",
            config_text,
        ):
            package_roots.append(base_root / "Lib" / "site-packages")
    package_roots = [item.resolve() for item in package_roots if item.is_dir()]
    if not package_roots:
        raise PortableBuildError(
            f"Python site-packages is missing below: {environment_root}"
        )
    try:
        result = subprocess.run(
            [
                str(executable),
                "-I",
                "-c",
                "import platform; print(platform.python_version())",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PortableBuildError(
            f"cannot execute target Python: {executable}"
        ) from error
    version = result.stdout.strip()
    if not re.fullmatch(r"3\.11(?:\.\d+)?", version):
        raise PortableBuildError(
            f"AiPeople portable runtime requires Python 3.11, found {version or 'unknown'}"
        )
    return PythonLayout(
        environment_root=environment_root,
        base_root=base_root,
        executable=executable.resolve(),
        package_roots=tuple(package_roots),
        version=version,
    )


def resolve_distributions(
    package_roots: Sequence[Path],
    seeds: Sequence[str],
    allowed_missing: Sequence[str] = (),
) -> tuple[list[SelectedDistribution], list[str]]:
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except ImportError as error:
        raise PortableBuildError(
            "the build interpreter needs the 'packaging' package"
        ) from error

    available: dict[str, importlib.metadata.Distribution] = {}
    discovered = list(
        importlib.metadata.distributions(path=[str(item) for item in package_roots])
    )
    discovered.sort(
        key=lambda dist: (
            _canonical_name(dist.metadata.get("Name", "")),
            str(getattr(dist, "_path", "")).casefold(),
        )
    )
    for distribution in discovered:
        name = distribution.metadata.get("Name")
        if name:
            available.setdefault(_canonical_name(name), distribution)

    allowed = {_canonical_name(item) for item in allowed_missing}
    selected: dict[str, SelectedDistribution] = {}
    queue: list[tuple[str, str]] = [(item, "runtime seed") for item in seeds]
    ignored_missing: set[str] = set()
    missing: dict[str, set[str]] = {}
    while queue:
        requested, parent = queue.pop(0)
        key = _canonical_name(requested)
        if key in selected or key in ignored_missing:
            continue
        distribution = available.get(key)
        if distribution is None:
            if key in allowed:
                ignored_missing.add(key)
                continue
            missing.setdefault(requested, set()).add(parent)
            continue
        name = distribution.metadata.get("Name", requested)
        version = distribution.version
        selected[key] = SelectedDistribution(name, version, distribution)
        for raw_requirement in distribution.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as error:
                raise PortableBuildError(
                    f"invalid dependency metadata in {name}: {raw_requirement}"
                ) from error
            try:
                enabled = requirement.marker is None or requirement.marker.evaluate(
                    {"extra": ""}
                )
            except Exception as error:
                raise PortableBuildError(
                    f"cannot evaluate dependency marker in {name}: {raw_requirement}"
                ) from error
            if enabled:
                queue.append((requirement.name, f"dependency of {name}"))
    if missing:
        details = "; ".join(
            f"{name} ({', '.join(sorted(parents))})"
            for name, parents in sorted(
                missing.items(), key=lambda item: item[0].casefold()
            )
        )
        raise PortableBuildError(
            "required Python distributions are missing: "
            f"{details}. Install them in --python-root or explicitly acknowledge each "
            "with --allow-missing-package."
        )
    result = sorted(selected.values(), key=lambda item: _canonical_name(item.name))
    return result, sorted(ignored_missing)


def _add_python_runtime(
    builder: PlanBuilder,
    layout: PythonLayout,
    distributions: Sequence[SelectedDistribution],
) -> None:
    base_root = layout.base_root
    root_files: set[Path] = set()
    for pattern in ("python*.exe", "python*.dll", "vcruntime*.dll"):
        root_files.update(item for item in base_root.glob(pattern) if item.is_file())
    license_path = base_root / "LICENSE.txt"
    if license_path.is_file():
        root_files.add(license_path)
    if not any(item.name.casefold() == "python.exe" for item in root_files):
        raise PortableBuildError(f"base Python runtime is incomplete: {base_root}")
    for source in sorted(root_files, key=lambda item: item.name.casefold()):
        builder.add_file(
            source, PurePosixPath("runtime") / source.name, "python-stdlib"
        )
    builder.add_tree(base_root / "DLLs", "runtime/DLLs", "python-stdlib")

    stdlib = base_root / "Lib"
    if not stdlib.is_dir():
        raise PortableBuildError(f"Python standard library is missing: {stdlib}")
    for source, relative in _walk_files(stdlib, "python-stdlib", builder._exclusions):
        if relative.parts and relative.parts[0].casefold() == "site-packages":
            continue
        builder.add_file(
            source,
            PurePosixPath("runtime/Lib") / PurePosixPath(*relative.parts),
            "python-stdlib",
        )

    missing_distribution_files: list[str] = []
    for selected in distributions:
        files = selected.distribution.files
        if files is None:
            raise PortableBuildError(
                f"distribution has no installed-file record: {selected.name}"
            )
        for package_path in sorted(files, key=lambda item: str(item).casefold()):
            source = Path(selected.distribution.locate_file(package_path)).resolve()
            destination = layout.destination_for(source)
            if destination is None:
                raise PortableBuildError(
                    f"distribution file escapes the Python environment: {selected.name}"
                )
            if destination.parts[0].casefold() != "lib":
                # Console entry points are not used; the launcher invokes python.exe.
                builder.exclude("python-package:non-library-entry")
                continue
            directory_reason = None
            parent = Path(*destination.parts[:-1])
            for index in range(1, len(parent.parts) + 1):
                directory_reason = _excluded_directory_reason(
                    Path(*parent.parts[:index]), "python-package"
                )
                if directory_reason is not None:
                    break
            if directory_reason is not None:
                builder.exclude(directory_reason)
                continue
            if not _is_regular_file(source):
                missing_distribution_files.append(
                    f"{selected.name}:{PurePosixPath(*destination.parts)}"
                )
                continue
            builder.add_file(
                source,
                PurePosixPath("runtime") / destination,
                "python-package",
            )
    if missing_distribution_files:
        preview = ", ".join(missing_distribution_files[:8])
        suffix = " ..." if len(missing_distribution_files) > 8 else ""
        raise PortableBuildError(
            f"installed distribution records reference missing files: {preview}{suffix}"
        )


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PortableBuildError(f"invalid {label}: {path}") from error
    if not isinstance(value, dict):
        raise PortableBuildError(f"{label} must be a JSON object: {path}")
    return value


def _resolve_asset_reference(root: Path, raw: object, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise PortableBuildError(f"asset manifest has invalid {label}")
    candidate = Path(raw)
    if (
        candidate.is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", raw)
        or raw.startswith("\\\\")
    ):
        raise PortableBuildError(f"asset manifest {label} must be relative")
    resolved = (root / candidate).resolve()
    if not _is_within(resolved, root.resolve()):
        raise PortableBuildError(f"asset manifest {label} escapes --assets")
    return resolved


def validate_assets(assets: Path) -> dict[str, Path]:
    assets = assets.resolve()
    if not assets.is_dir():
        raise PortableBuildError(f"portable assets directory is missing: {assets}")
    for relative in REQUIRED_ASSET_FILES:
        path = assets / Path(relative)
        if not path.is_file():
            raise PortableBuildError(
                f"portable assets are incomplete: missing {relative}"
            )
    summary = _load_json_object(
        assets / "portable_assets.json", "portable asset summary"
    )
    if summary.get("format") != "portable-bitsandbytes-nf4-base-plus-separate-lora":
        raise PortableBuildError(
            "--assets was not produced by build_portable_assets.py"
        )
    model_manifest_name = summary.get("model_manifest")
    model_manifest_path = _resolve_asset_reference(
        assets, model_manifest_name, "model_manifest"
    )
    model_manifest = _load_json_object(model_manifest_path, "Gemma manifest")
    model_root = _resolve_asset_reference(
        model_manifest_path.parent, model_manifest.get("model_root"), "model_root"
    )
    adapter_root = _resolve_asset_reference(
        model_manifest_path.parent, model_manifest.get("adapter_root"), "adapter_root"
    )
    retrieval_manifest_path = assets / "retrieval" / "retrieval_assets.json"
    retrieval_manifest = _load_json_object(
        retrieval_manifest_path, "retrieval asset manifest"
    )
    bge_root = _resolve_asset_reference(
        retrieval_manifest_path.parent,
        (retrieval_manifest.get("bge") or {}).get("path")
        if isinstance(retrieval_manifest.get("bge"), dict)
        else None,
        "bge.path",
    )
    qwen_root = _resolve_asset_reference(
        retrieval_manifest_path.parent,
        (retrieval_manifest.get("reranker") or {}).get("path")
        if isinstance(retrieval_manifest.get("reranker"), dict)
        else None,
        "reranker.path",
    )
    for label, path in (
        ("model_root", model_root),
        ("adapter_root", adapter_root),
        ("bge.path", bge_root),
        ("reranker.path", qwen_root),
    ):
        if not path.is_dir():
            raise PortableBuildError(f"asset manifest target is missing: {label}")
    return {
        "assets": assets,
        "model_manifest": model_manifest_path,
        "gemma": model_root,
        "adapter": adapter_root,
        "retrieval_manifest": retrieval_manifest_path,
        "bge": bge_root,
        "qwen": qwen_root,
    }


def _declared_asset_files(
    manifest_path: Path,
    package_root: Path,
    fields: Sequence[str],
) -> list[Path]:
    manifest = _load_json_object(manifest_path, "asset package manifest")
    result: list[Path] = []
    for field in fields:
        records = manifest.get(field)
        if not isinstance(records, list):
            raise PortableBuildError(
                f"asset manifest field must be an array: {manifest_path.name}:{field}"
            )
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise PortableBuildError(
                    f"invalid asset record: {manifest_path.name}:{field}[{index}]"
                )
            source = _resolve_asset_reference(
                package_root,
                record.get("path"),
                f"{manifest_path.name}:{field}[{index}].path",
            )
            if not _is_regular_file(source):
                raise PortableBuildError(
                    f"declared asset file is missing: {manifest_path.name}:{field}[{index}]"
                )
            expected_size = record.get("bytes", record.get("size_bytes"))
            if not isinstance(expected_size, int) or expected_size < 0:
                raise PortableBuildError(
                    f"declared asset size is invalid: {manifest_path.name}:{field}[{index}]"
                )
            if _lstat(source).st_size != expected_size:
                raise PortableBuildError(
                    f"declared asset size does not match: {manifest_path.name}:{field}[{index}]"
                )
            result.append(source)
    return result


def _add_assets_files(
    builder: PlanBuilder,
    asset_roots: dict[str, Path],
) -> None:
    assets = asset_roots["assets"]
    model_manifest = asset_roots["model_manifest"]
    retrieval_manifest = asset_roots["retrieval_manifest"]
    bge_manifest = asset_roots["bge"] / "bge_manifest.json"
    qwen_manifest = asset_roots["qwen"] / "reranker_manifest.json"
    required: set[Path] = {
        assets / "portable_assets.json",
        model_manifest,
        retrieval_manifest,
        bge_manifest,
        qwen_manifest,
    }
    required.update(
        _declared_asset_files(
            model_manifest,
            asset_roots["gemma"],
            ("model_files",),
        )
    )
    required.update(
        _declared_asset_files(
            model_manifest,
            asset_roots["adapter"],
            ("adapter_files",),
        )
    )
    required.update(
        _declared_asset_files(
            bge_manifest,
            asset_roots["bge"],
            ("model_files",),
        )
    )
    required.update(
        _declared_asset_files(
            qwen_manifest,
            asset_roots["qwen"],
            ("weights", "tokenizer_files"),
        )
    )
    resolved_required = {item.resolve() for item in required}
    for source in sorted(
        resolved_required,
        key=lambda item: str(item.relative_to(assets)).casefold(),
    ):
        if not _is_regular_file(source):
            raise PortableBuildError(
                f"required portable asset is missing: {source.name}"
            )
        relative = source.relative_to(assets)
        builder.add_file(
            source,
            PurePosixPath("assets") / PurePosixPath(*relative.parts),
            "assets",
            scan_secrets=True,
        )

    # Enumerate only to account for excluded extras. No unlisted file can enter
    # the release, even when a smoke test writes into the portable-assets input.
    for source, _relative in _walk_files(assets, "assets", builder._exclusions):
        if source.resolve() not in resolved_required:
            builder.exclude("assets:not-declared")


def _find_document(root: Path, kind: str) -> Path | None:
    if not root.is_dir():
        return None
    stems = {
        "LICENSE": ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENCE.txt"),
        "README": ("README.md", "README.txt", "README"),
        "NOTICE": ("NOTICE", "NOTICE.txt", "NOTICE.md"),
    }[kind]
    entries = {item.name.casefold(): item for item in root.iterdir() if item.is_file()}
    for name in stems:
        candidate = entries.get(name.casefold())
        if candidate is not None and _is_regular_file(candidate):
            return candidate.resolve()
    return None


def _valid_license_text(path: Path) -> bool:
    try:
        payload = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    normalized = payload.strip().casefold()
    if len(normalized) < 200:
        return False
    if re.search(r"(?m)^\s*licen[cs]e\s*:\s*licen[cs]e\s*$", normalized):
        return False
    return normalized not in {"todo", "tbd", "placeholder"}


def _collect_licenses(
    builder: PlanBuilder,
    layout: PythonLayout,
    app_root: Path,
    asset_roots: dict[str, Path],
) -> tuple[list[LicenseRecord], list[str], list[str]]:
    records: list[LicenseRecord] = []
    missing: list[str] = []
    warnings: list[str] = []

    python_license = layout.base_root / "LICENSE.txt"
    if not _is_regular_file(python_license):
        raise PortableBuildError(f"Python LICENSE.txt is missing: {python_license}")
    python_destination = PurePosixPath("licenses/Python/LICENSE.txt")
    builder.add_file(python_license, python_destination, "license", scan_secrets=True)
    records.append(
        LicenseRecord("Python", "LICENSE", python_destination, "Python runtime")
    )

    repository_fallbacks: dict[str, tuple[Path, ...]] = {
        "Gemma": (app_root / "training_packages" / "models" / "Gemma-4-12B-it",),
        "BGE": (
            app_root / "local_runtime" / "models" / "retrieval" / "bge-small-zh-v1.5",
        ),
        "Qwen": (app_root / "training_packages" / "models" / "Qwen3-14B",),
    }
    component_roots = {
        "Gemma": asset_roots["gemma"],
        "BGE": asset_roots["bge"],
        "Qwen": asset_roots["qwen"],
    }
    for component, asset_root in component_roots.items():
        roots = (asset_root, *repository_fallbacks[component])
        for kind in ("LICENSE", "README", "NOTICE"):
            source = next(
                (
                    candidate
                    for root in roots
                    if (candidate := _find_document(root, kind))
                ),
                None,
            )
            if source is None or (
                kind == "LICENSE" and not _valid_license_text(source)
            ):
                marker = f"{component}:{kind}"
                missing.append(marker)
                warnings.append(f"missing upstream document: {marker}")
                continue
            suffix = source.suffix if source.suffix else ".txt"
            destination = PurePosixPath("licenses") / component / f"{kind}{suffix}"
            builder.add_file(source, destination, "license", scan_secrets=True)
            source_label = (
                "portable assets"
                if _is_within(source, asset_roots["assets"])
                else "repository upstream cache"
            )
            records.append(LicenseRecord(component, kind, destination, source_label))

    adapter_license = _find_document(asset_roots["adapter"], "LICENSE")
    if adapter_license is None or not _valid_license_text(adapter_license):
        missing.append("Adapter:LICENSE")
        warnings.append(
            "missing valid Adapter license; placeholder README metadata is not a license"
        )
    else:
        suffix = adapter_license.suffix if adapter_license.suffix else ".txt"
        destination = PurePosixPath("licenses/Adapter") / f"LICENSE{suffix}"
        builder.add_file(adapter_license, destination, "license", scan_secrets=True)
        records.append(
            LicenseRecord("Adapter", "LICENSE", destination, "portable assets")
        )
    return records, sorted(set(missing)), warnings


def _validate_launcher(launcher: Path) -> Path:
    launcher = launcher.expanduser().resolve()
    if launcher.suffix.casefold() != ".exe" or not _is_regular_file(launcher):
        raise PortableBuildError(f"launcher must be a regular .exe file: {launcher}")
    try:
        with open(_long_path(launcher), "rb") as stream:
            if stream.read(2) != b"MZ":
                raise PortableBuildError(
                    f"launcher is not a Windows PE executable: {launcher}"
                )
    except OSError as error:
        raise PortableBuildError(f"cannot inspect launcher: {launcher}") from error
    return launcher


def _add_app_files(builder: PlanBuilder, app_root: Path) -> None:
    builder.add_file(
        app_root / Path(USER_README_SOURCE),
        "README.txt",
        "documentation",
        scan_secrets=True,
    )
    for relative in APP_SINGLE_FILES:
        builder.add_file(
            app_root / Path(relative),
            PurePosixPath("app") / PurePosixPath(relative),
            "app",
            scan_secrets=True,
        )
    for source_relative, destination_relative in APP_TREES:
        builder.add_tree(
            app_root / Path(source_relative),
            PurePosixPath("app") / PurePosixPath(destination_relative),
            "app",
            scan_secrets=True,
        )
    canon_source = app_root.parent / "人物设定"
    if not canon_source.is_dir():
        canon_source = app_root / "人物设定"
    for character in ("主角", "白未晞"):
        builder.add_tree(
            canon_source / character,
            PurePosixPath("app/人物设定") / character,
            "app",
            scan_secrets=True,
        )


def create_build_plan(
    *,
    assets: Path,
    launcher: Path,
    python_root: Path,
    app_root: Path = APP_ROOT,
    extra_runtime_packages: Sequence[str] = (),
    allowed_missing_packages: Sequence[str] = (),
) -> tuple[BuildPlan, PythonLayout]:
    app_root = app_root.expanduser().resolve()
    if not app_root.is_dir():
        raise PortableBuildError(f"application root is missing: {app_root}")
    asset_roots = validate_assets(assets.expanduser().resolve())
    launcher = _validate_launcher(launcher)
    layout = inspect_python_layout(python_root)
    distributions, ignored_missing = resolve_distributions(
        layout.package_roots,
        (*DEFAULT_RUNTIME_PACKAGES, *extra_runtime_packages),
        allowed_missing_packages,
    )
    builder = PlanBuilder()
    builder.add_file(launcher, "AiPeople.exe", "launcher")
    _add_python_runtime(builder, layout, distributions)
    _add_app_files(builder, app_root)
    _add_assets_files(builder, asset_roots)
    licenses, missing_licenses, license_warnings = _collect_licenses(
        builder, layout, app_root, asset_roots
    )
    warnings = list(license_warnings)
    if ignored_missing:
        warnings.append(
            "explicitly allowed missing Python distributions: "
            + ", ".join(ignored_missing)
        )
    return (
        BuildPlan(
            files=builder.files,
            distributions=list(distributions),
            exclusions=builder.exclusions,
            warnings=warnings,
            licenses=licenses,
            missing_licenses=missing_licenses,
        ),
        layout,
    )


def _validate_output_path(
    output: Path,
    *,
    sources: Sequence[Path],
) -> Path:
    output = output.expanduser().resolve()
    if output == Path(output.anchor) or output == Path.home().resolve():
        raise PortableBuildError(f"refusing unsafe output path: {output}")
    for source in sources:
        source = source.expanduser().resolve()
        if _is_within(source, output) or _is_within(output, source):
            raise PortableBuildError(
                f"output must not contain or be inside an input path: {output} / {source}"
            )
    if not output.name or output.name in {".", ".."}:
        raise PortableBuildError(f"refusing unsafe output path: {output}")
    return output


def _mkdir(path: Path) -> None:
    os.makedirs(_long_path(path), exist_ok=True)


def _copy_and_hash(source: Path, destination: Path) -> str:
    _mkdir(destination.parent)
    digest = hashlib.sha256()
    try:
        with (
            open(_long_path(source), "rb") as reader,
            open(_long_path(destination), "xb") as writer,
        ):
            while chunk := reader.read(COPY_CHUNK_BYTES):
                writer.write(chunk)
                digest.update(chunk)
        shutil.copystat(
            _long_path(source), _long_path(destination), follow_symlinks=False
        )
    except BaseException:
        try:
            os.unlink(_long_path(destination))
        except OSError:
            pass
        raise
    return digest.hexdigest()


def _category_summary(files: Sequence[PlannedFile]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for item in files:
        record = result.setdefault(item.category, {"files": 0, "bytes": 0})
        record["files"] += 1
        record["bytes"] += item.size
    return dict(sorted(result.items()))


def _payload_digest(entries: Sequence[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(entry["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(entry["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _manifest(
    plan: BuildPlan,
    layout: PythonLayout,
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "format": "aipeople-windows-portable-directory",
        "platform": "win-x64",
        "entrypoint": "app/tools/baiweixi_chat_app.py",
        "launcher": "AiPeople.exe",
        "python": {
            "version": layout.version,
            "executable": "runtime/python.exe",
            "distributions": [
                {"name": item.name, "version": item.version}
                for item in plan.distributions
            ],
        },
        "directories": ["app", "assets", "data", "licenses", "logs", "runtime"],
        "licenses": [
            {
                "component": item.component,
                "kind": item.kind,
                "path": str(item.destination),
                "source": item.source_label,
            }
            for item in plan.licenses
        ],
        "missing_licenses": plan.missing_licenses,
        "warnings": plan.warnings,
        "exclusions": plan.exclusions,
        "payload_file_count": len(entries),
        "payload_total_bytes": sum(int(item["bytes"]) for item in entries),
        "payload_sha256": _payload_digest(entries),
        "files": list(entries),
    }


def _write_json(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")
    _mkdir(path.parent)
    with open(_long_path(path), "xb") as stream:
        stream.write(payload)


def _remove_known_temporary(path: Path, output: Path) -> None:
    if path.parent.resolve() != output.parent.resolve():
        raise PortableBuildError(f"refusing to remove unexpected path: {path}")
    allowed_prefixes = (f".{output.name}.staging-", f".{output.name}.backup-")
    if not path.name.startswith(allowed_prefixes):
        raise PortableBuildError(f"refusing to remove unexpected path: {path}")
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(_long_path(path))
    else:
        os.unlink(_long_path(path))


def _publish_staging(staging: Path, output: Path, replace: bool) -> Path | None:
    backup: Path | None = None
    if output.exists():
        if not replace:
            raise PortableBuildError(
                f"output already exists: {output}; pass --replace to replace it safely"
            )
        backup = output.parent / f".{output.name}.backup-{uuid.uuid4().hex}"
        os.replace(_long_path(output), _long_path(backup))
    try:
        os.replace(_long_path(staging), _long_path(output))
    except BaseException:
        if backup is not None and backup.exists() and not output.exists():
            os.replace(_long_path(backup), _long_path(output))
        raise
    return backup


def build_portable(
    *,
    assets: Path,
    output: Path,
    launcher: Path,
    python_root: Path,
    app_root: Path = APP_ROOT,
    replace: bool = False,
    dry_run: bool = False,
    extra_runtime_packages: Sequence[str] = (),
    allowed_missing_packages: Sequence[str] = (),
) -> dict[str, Any]:
    assets = assets.expanduser().resolve()
    launcher = launcher.expanduser().resolve()
    python_root = python_root.expanduser().resolve()
    app_root = app_root.expanduser().resolve()
    output = _validate_output_path(
        output,
        sources=(assets, launcher, python_root, app_root),
    )
    if output.exists() and not replace:
        raise PortableBuildError(
            f"output already exists: {output}; pass --replace to replace it safely"
        )
    if output.exists() and (not output.is_dir() or _is_reparse(output)):
        raise PortableBuildError(
            f"refusing to replace a non-directory or linked output: {output}"
        )
    plan, layout = create_build_plan(
        assets=assets,
        launcher=launcher,
        python_root=python_root,
        app_root=app_root,
        extra_runtime_packages=extra_runtime_packages,
        allowed_missing_packages=allowed_missing_packages,
    )
    summary: dict[str, Any] = {
        "status": "planned" if dry_run else "built",
        "output": str(output),
        "payload_file_count": len(plan.files),
        "payload_total_bytes": plan.total_bytes,
        "categories": _category_summary(plan.files),
        "python_version": layout.version,
        "python_distribution_count": len(plan.distributions),
        "missing_licenses": plan.missing_licenses,
        "warnings": plan.warnings,
        "exclusions": plan.exclusions,
    }
    if dry_run:
        return summary

    _mkdir(output.parent)
    free_bytes = shutil.disk_usage(output.parent).free
    required_free = plan.total_bytes + DISK_HEADROOM_BYTES
    if free_bytes < required_free:
        raise PortableBuildError(
            "insufficient free space for staging: "
            f"need at least {required_free} bytes, found {free_bytes}"
        )
    staging = output.parent / f".{output.name}.staging-{uuid.uuid4().hex}"
    backup: Path | None = None
    try:
        _mkdir(staging)
        for relative in ("app", "assets", "data", "licenses", "logs", "runtime"):
            _mkdir(staging / relative)
        entries: list[dict[str, Any]] = []
        for item in plan.files:
            destination = staging.joinpath(*item.destination.parts)
            digest = _copy_and_hash(item.source, destination)
            copied_size = _lstat(destination).st_size
            if copied_size != item.size:
                raise PortableBuildError(
                    f"copied file size changed: {item.destination}"
                )
            entries.append(
                {
                    "path": str(item.destination),
                    "bytes": copied_size,
                    "sha256": digest,
                    "category": item.category,
                }
            )
        entries.sort(key=lambda item: (item["path"].casefold(), item["path"]))
        manifest = _manifest(plan, layout, entries)
        manifest_path = staging / "release_manifest.json"
        _write_json(manifest_path, manifest)
        manifest_size = manifest_path.stat().st_size
        backup = _publish_staging(staging, output, replace)
        summary["release_manifest"] = str(output / "release_manifest.json")
        summary["release_manifest_bytes"] = manifest_size
        summary["distribution_total_bytes"] = plan.total_bytes + manifest_size
        summary["payload_sha256"] = manifest["payload_sha256"]
    except BaseException:
        if staging.exists():
            _remove_known_temporary(staging, output)
        raise
    finally:
        if backup is not None and backup.exists():
            try:
                _remove_known_temporary(backup, output)
            except OSError as error:
                summary.setdefault("warnings", []).append(
                    f"old output remains at {backup.name}: {error}"
                )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the reproducible Windows portable AiPeople directory. "
            "Model files are accepted only from a prebuilt portable-assets directory."
        )
    )
    parser.add_argument(
        "--assets",
        type=Path,
        default=DEFAULT_ASSETS,
        help=f"portable-assets input (default: {DEFAULT_ASSETS})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"final portable directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--launcher",
        type=Path,
        required=True,
        help="compiled Windows launcher executable",
    )
    parser.add_argument(
        "--python-root",
        type=Path,
        default=Path(sys.prefix),
        help="Python 3.11 installation or virtual environment to copy",
    )
    parser.add_argument(
        "--app-root",
        type=Path,
        default=APP_ROOT,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--replace",
        "--force",
        action="store_true",
        help="replace an existing output only after staging succeeds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print sizes without copying or changing output",
    )
    parser.add_argument(
        "--extra-runtime-package",
        action="append",
        default=[],
        metavar="NAME",
        help="add a distribution seed; may be repeated",
    )
    parser.add_argument(
        "--allow-missing-package",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "explicitly acknowledge a missing metadata dependency; may be repeated "
            "and is recorded as a release warning"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        summary = build_portable(
            assets=args.assets,
            output=args.output,
            launcher=args.launcher,
            python_root=args.python_root,
            app_root=args.app_root,
            replace=args.replace,
            dry_run=args.dry_run,
            extra_runtime_packages=args.extra_runtime_package,
            allowed_missing_packages=args.allow_missing_package,
        )
    except PortableBuildError as error:
        parser.exit(2, f"error: {error}\n")
    for warning in summary.get("warnings", ()):
        print(f"warning: {warning}", file=sys.stderr)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
