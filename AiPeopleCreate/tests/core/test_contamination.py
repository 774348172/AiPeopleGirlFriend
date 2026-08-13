"""profile contamination 静态扫描：核心代码不得出现生产角色知识。"""
from __future__ import annotations

import textwrap

from data_gen_v4.core.contamination import (
    ContaminationViolation,
    discover_profile_terms,
    scan_directory,
    scan_file,
)


def test_scan_file_finds_term_in_comment(tmp_path):
    source = tmp_path / "leaky.py"
    source.write_text(
        textwrap.dedent(
            """
            # 这个角色叫秦未晞，住在河畔公寓
            def reply():
                return "你好"
            """
        ),
        encoding="utf-8",
    )
    violations = scan_file(source, {"秦未晞"})
    assert len(violations) == 1
    assert violations[0].term == "秦未晞"
    assert violations[0].line == 2


def test_scan_file_finds_term_in_string_literal(tmp_path):
    source = tmp_path / "leaky.py"
    source.write_text(
        'NAME = "秦未晞"\n',
        encoding="utf-8",
    )
    violations = scan_file(source, {"秦未晞"})
    assert [v.line for v in violations] == [1]


def test_scan_file_ignores_identifier_that_contains_term_but_not_literal(tmp_path):
    # 仅标识符中含词（如变量名 profile_id）不算敏感词命中——扫描目标是字符串与注释
    source = tmp_path / "clean.py"
    source.write_text(
        textwrap.dedent(
            """
            def build_profile_id():
                return "fixture.alpha"
            """
        ),
        encoding="utf-8",
    )
    violations = scan_file(source, {"profile_id"})
    # "fixture.alpha" 与 "profile_id" 无关；函数名/参数名不命中
    assert violations == []


def test_scan_file_no_terms_returns_empty(tmp_path):
    source = tmp_path / "clean.py"
    source.write_text("x = 1\n", encoding="utf-8")
    assert scan_file(source, set()) == []


def test_scan_directory_recursive(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "gen.py").write_text('EVENT = "五岁搬家"\n', encoding="utf-8")
    (tmp_path / "core" / "sub").mkdir()
    (tmp_path / "core" / "sub" / "extra.py").write_text('x = "五岁搬家"\n', encoding="utf-8")
    violations = scan_directory(tmp_path / "core", {"五岁搬家"})
    assert len(violations) == 2
    assert all(isinstance(v, ContaminationViolation) for v in violations)


def test_discover_profile_terms_from_profiles_dir(tmp_path):
    profile_dir = tmp_path / "profiles"
    (profile_dir / "fixture.alpha").mkdir(parents=True)
    (profile_dir / "fixture.alpha" / "manifest.json").write_text(
        '{"profile_id": "fixture.alpha"}', encoding="utf-8"
    )
    (profile_dir / "fixture.beta").mkdir()
    (profile_dir / "fixture.beta" / "manifest.yaml").write_text(
        "profile_id: fixture.beta", encoding="utf-8"
    )
    terms = discover_profile_terms(profile_dir)
    assert terms == {"fixture.alpha", "fixture.beta"}


def test_discover_profile_terms_missing_dir_returns_empty(tmp_path):
    assert discover_profile_terms(tmp_path / "nope") == set()


def test_scan_self_does_not_find_production_names():
    """阶段 1 自检：core 源码中不得出现任何生产角色名（硬标准 #3）。"""
    import pathlib

    core_dir = pathlib.Path(__file__).resolve().parents[2] / "data_gen_v4" / "core"
    violations = scan_directory(core_dir, {"秦未晞", "于谦", "qinweixi", "yuqian"})
    assert violations == [], [str(v) for v in violations]
