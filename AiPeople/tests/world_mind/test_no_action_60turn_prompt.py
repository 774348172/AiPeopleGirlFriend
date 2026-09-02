from __future__ import annotations

from tools import run_baiweixi_60turn_player_simulation as simulation


def test_all_60_turns_require_dialogue_only_output() -> None:
    assert len(simulation.TURNS) == 60

    for turn in simulation.TURNS:
        prompt = simulation._system(turn)
        assert "[输出形式硬约束]" in prompt
        assert "最终回复只能包含男主能直接听见的对白纯文本" in prompt
        assert "即使历史回复中含有动作旁白" in prompt
        assert "[允许表达的动作]\n无" in prompt
