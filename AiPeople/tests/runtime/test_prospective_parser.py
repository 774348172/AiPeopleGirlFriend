from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime._prospective_parser import parse_prospective_command


NOW = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)  # 12:00 Asia/Shanghai


def parse(text: str, *, now: datetime = NOW, zone: str = "Asia/Shanghai"):
    return parse_prospective_command(text, occurred_at=now, timezone_name=zone)


@pytest.mark.parametrize(
    ("text", "kind", "description", "due"),
    [
        ("明天下午3点提醒我交材料", "reminder", "交材料", "2026-08-06T07:00:00+00:00"),
        ("请在后天上午9点半提醒我打电话", "reminder", "打电话", "2026-08-07T01:30:00+00:00"),
        ("2026-08-08 18:20提醒我关窗", "reminder", "关窗", "2026-08-08T10:20:00+00:00"),
        ("我们约好明天下午3点一起看电影", "shared_plan", "一起看电影", "2026-08-06T07:00:00+00:00"),
        ("我答应你明天下午3点交材料", "promise", "交材料", "2026-08-06T07:00:00+00:00"),
    ],
)
def test_explicit_create_with_exact_time(text, kind, description, due) -> None:
    result = parse(text)
    assert result.disposition == "executable"
    assert result.command is not None
    assert result.command.action == "create"
    assert result.command.kind == kind
    assert result.command.description == description
    assert result.command.initial_state == "confirmed"
    assert result.command.due_at.isoformat() == due


@pytest.mark.parametrize(
    ("text", "kind", "description", "reason"),
    [
        ("提醒我交材料", "reminder", "交材料", "exact_time_required"),
        ("周末提醒我交材料", "reminder", "交材料", "exact_time_required"),
        ("明天下午3点左右提醒我交材料", "reminder", "交材料", "ambiguous_time"),
        ("我们计划周末一起看电影", "shared_plan", "一起看电影", "exact_time_required"),
        ("我答应你过几天交材料", "promise", "交材料", "exact_time_required"),
        ("今天上午9点提醒我交材料", "reminder", "交材料", "past_time"),
    ],
)
def test_ambiguous_or_incomplete_time_stays_proposed(
    text, kind, description, reason
) -> None:
    result = parse(text)
    assert result.disposition == "needs_confirmation"
    assert result.reason == reason
    assert result.command is not None
    assert result.command.kind == kind
    assert result.command.description == description
    assert result.command.initial_state == "proposed"
    assert result.command.due_at is None


def test_explicit_promise_without_time_can_be_confirmed() -> None:
    result = parse("我答应你不再熬夜")
    assert result.disposition == "executable"
    assert result.command is not None
    assert result.command.kind == "promise"
    assert result.command.initial_state == "confirmed"
    assert result.command.description == "不再熬夜"
    assert result.command.due_at is None


@pytest.mark.parametrize(
    ("text", "action", "kind", "target"),
    [
        ("取消提醒：交材料", "cancel", "reminder", "交材料"),
        ("请取消计划：周末一起看电影", "cancel", "shared_plan", "周末一起看电影"),
        ("提醒：交材料已经完成了", "complete", "reminder", "交材料"),
        ("我已完成承诺：不再熬夜", "complete", "promise", "不再熬夜"),
        ("确认承诺：不再熬夜", "confirm", "promise", "不再熬夜"),
    ],
)
def test_explicit_transition_commands(text, action, kind, target) -> None:
    result = parse(text)
    assert result.disposition == "executable"
    assert result.command is not None
    assert result.command.action == action
    assert result.command.kind == kind
    assert result.command.target_phrase == target
    assert result.command.description is None


def test_reminder_confirmation_requires_exact_time() -> None:
    ambiguous = parse("确认提醒：交材料")
    assert ambiguous.disposition == "needs_confirmation"
    assert ambiguous.command is not None
    assert ambiguous.command.target_phrase == "交材料"
    assert ambiguous.command.due_at is None

    exact = parse("确认提醒：交材料，时间是明天下午3点")
    assert exact.disposition == "executable"
    assert exact.command is not None
    assert exact.command.target_phrase == "交材料"
    assert exact.command.due_at.isoformat() == "2026-08-06T07:00:00+00:00"


@pytest.mark.parametrize(
    "text",
    [
        "你好",
        "今天天气怎么样",
        "你能提醒我吗？",
        "不要提醒我交材料",
        "如果明天下雨就提醒我带伞",
        "她说明天下午3点提醒我交材料",
        "你昨天提醒我交材料",
        "计划取消了吗？",
        "我保证这是真的",
        "确认提醒：这个",
        "取消计划：那个",
        "完成了",
    ],
)
def test_non_commands_and_ambiguous_targets_do_not_emit_commands(text) -> None:
    result = parse(text)
    assert result.disposition == "no_command"
    assert result.command is None


@pytest.mark.parametrize(
    "text",
    [
        "2026年2月30日15点提醒我交材料",
        "明天25点提醒我交材料",
        "明天下午3点或4点提醒我交材料",
    ],
)
def test_invalid_or_multiple_time_never_becomes_confirmed(text) -> None:
    result = parse(text)
    assert result.disposition == "needs_confirmation"
    assert result.command is not None
    assert result.command.initial_state == "proposed"
    assert result.command.due_at is None


def test_same_input_time_and_timezone_is_byte_stable() -> None:
    first = parse("明天下午3点提醒我交材料")
    second = parse("明天下午3点提醒我交材料")
    assert first == second


def test_invalid_clock_and_timezone_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_prospective_command(
            "提醒我交材料",
            occurred_at=datetime(2026, 8, 5, 12, 0),
            timezone_name="Asia/Shanghai",
        )
    with pytest.raises(ValueError, match="IANA"):
        parse("提醒我交材料", zone="Mars/Olympus")
