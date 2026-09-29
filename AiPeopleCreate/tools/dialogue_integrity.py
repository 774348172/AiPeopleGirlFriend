"""Structural failures are hard errors; semantic uncertainty stays in review."""
import re


def validate_spoken_text(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('empty dialogue')
    if re.match(r'^\s*(男主|主角|白未晞|assistant|user)\s*[:：]', text):
        raise ValueError('speaker labels must be metadata, not utterance text')
    if not re.search(r'[\w\u4e00-\u9fff]', text):
        raise ValueError('empty dialogue or serialization fragment')
    if re.search(r'"(?:recent_turns|memory_turns|current_user|role|content)"\s*:', text) or text.strip() in (
        'recent_turns', 'memory_turns', 'current_user', '"recent_turns"', '"memory_turns"',
    ):
        raise ValueError('serialization fragment is not spoken dialogue')


def validate_dialogue_structure(row):
    for kind in ('memory_turns', 'recent_turns'):
        for i, turn in enumerate(row[kind]):
            if turn['speaker'] != ('男主' if i % 2 == 0 else '白未晞'):
                raise ValueError('speaker metadata swapped')
            validate_spoken_text(turn['text'])
    validate_spoken_text(row['current_user'])
    norm = lambda text: re.sub(r'[\W_]+', '', text)
    old = [norm(t['text']) for t in row['memory_turns']]
    recent = [norm(t['text']) for t in row['recent_turns']]
    if old == recent:
        raise ValueError('whole old dialogue replayed as recent dialogue')
