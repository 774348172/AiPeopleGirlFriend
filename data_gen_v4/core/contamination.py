"""profile contamination 静态扫描（《数据生成器v4设计》§3.8、§20 阶段 1）。

通用性硬标准 #3：核心代码、默认模板和核心测试中不得出现任何生产角色名、专属事件、
口头禅或文件路径。本模块扫描 core 源码（含注释与字符串），报告违规位置。

敏感词来源由调用方提供（profiles 目录发现的 profile_id / 角色名 / 事件关键词），
扫描器本身不内置任何角色知识。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ContaminationViolation:
    file: str
    line: int
    term: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: 命中敏感词 {self.term!r}"


def scan_directory(
    directory: Path,
    sensitive_terms: set[str],
) -> list[ContaminationViolation]:
    """扫描目录下所有 .py 文件（不递归排除 __pycache__）。"""
    violations: list[ContaminationViolation] = []
    if not sensitive_terms:
        return violations
    for path in sorted(Path(directory).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        violations.extend(scan_file(path, sensitive_terms))
    return violations


def scan_file(path: Path, sensitive_terms: set[str]) -> list[ContaminationViolation]:
    """对单个 .py 文件做两层扫描：
    1) AST 字符串字面量（含 f-string 常量片段、docstring）。
    2) 注释兜底（# 开头行与行内 # 之后的内容），覆盖 AST 不易覆盖的注释位置。

    标识符与变量名不参与扫描（`profile_id` 这类通用词是合法代码，不构成污染）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    violations: list[ContaminationViolation] = []
    found: set[tuple[int, str]] = set()

    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for term in sensitive_terms:
                    if term in node.value:
                        found.add((node.lineno, term))
            elif isinstance(node, ast.JoinedStr):
                for value in node.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        for term in sensitive_terms:
                            if term in value.value:
                                found.add((getattr(value, "lineno", node.lineno), term))
    except SyntaxError:
        # 语法错误时退化为注释扫描（扫描器不因源码损坏而漏报注释）
        pass

    for line_no, line in enumerate(text.splitlines(), start=1):
        hash_pos = line.find("#")
        if hash_pos < 0:
            continue
        comment = line[hash_pos:]
        for term in sensitive_terms:
            if term in comment:
                found.add((line_no, term))

    for line_no, term in sorted(found):
        violations.append(
            ContaminationViolation(file=str(path), line=line_no, term=term)
        )
    return violations


def discover_profile_terms(profiles_dir: Path) -> set[str]:
    """从 profiles 目录发现敏感词：目录名 + 各 manifest 中的 profile_id。
    阶段 3 建 profile 包后自动生效；不依赖任何生产角色知识。"""
    terms: set[str] = set()
    profiles_dir = Path(profiles_dir)
    if not profiles_dir.exists():
        return terms
    import json
    import re

    import yaml

    for manifest in profiles_dir.rglob("manifest.*"):
        try:
            if manifest.suffix == ".json":
                doc = json.loads(manifest.read_text(encoding="utf-8"))
            elif manifest.suffix in (".yaml", ".yml"):
                doc = yaml.safe_load(manifest.read_text(encoding="utf-8"))
            else:
                continue
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict):
            profile_id = doc.get("profile_id")
            if isinstance(profile_id, str) and re.match(r"^[a-z0-9][a-z0-9._-]*$", profile_id):
                terms.add(profile_id)
    for directory in profiles_dir.iterdir():
        if directory.is_dir():
            terms.add(directory.name)
    return {t for t in terms if t}
