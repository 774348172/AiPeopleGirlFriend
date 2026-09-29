import copy
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import pilot_pipeline
from dialogue_chain_scenarios import build_scenarios, validate_chains, validate_messages
from data_gen_v4.runtime_grounded.renderer import render_game_reply_context_from_model_view, build_game_reply_chat_messages, parse_game_reply_dialogue_lines

def test_final_context_keeps_all_original_turns():
    for row in build_scenarios():
        view = row['model_view']
        messages = [{'role': 'system', 'content': render_game_reply_context_from_model_view(view)}]
        messages += [{'role': r, 'content': c} for r, c in build_game_reply_chat_messages(parse_game_reply_dialogue_lines(tuple(view['recent_dialogue'])), character_display_name='白未晞', current_protagonist_utterance=view['current_protagonist_utterance'])]
        validate_messages(row, messages)
        with pytest.raises(ValueError):
            validate_messages(row, [messages[0], *messages[2:]])

def test_clock_change_cannot_hide_duplicate():
    row = build_scenarios(1)[0]
    other = copy.deepcopy(row)
    other['scenario_id'] = 'other'
    other['model_view']['snapshot']['captured_game_time'] = '2026-09-12 19:05'
    with pytest.raises(ValueError, match='重复'):
        validate_chains([row, other])

def test_wrong_memory_and_future_history_fail():
    row = build_scenarios(1)[0]
    row['model_view']['selected_memory_frame']['selected_memories'][0]['statement'] = '不相关的旧模板'
    with pytest.raises(ValueError, match='源对话'):
        validate_chains([row])
    row = build_scenarios(1)[0]
    row['recent_time'] = '2026-09-13 18:00'
    with pytest.raises(ValueError, match='时间'):
        validate_chains([row])

def test_no_repeat_fill():
    with pytest.raises(ValueError):
        build_scenarios(11)
