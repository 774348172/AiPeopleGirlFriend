"""Check final-build desktop stage evidence; not a target-hardware or soak acceptance."""
import argparse
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('folder', type=Path)
args = parser.parse_args()
root = args.folder
native = json.loads((root / 'native.json').read_text(encoding='utf-8'))
files = list(root.glob('main-*-frames.csv'))
if len(files) != 1:
    raise RuntimeError('Use a fresh output directory with exactly one main process capture')
with files[0].open() as stream:
    frames = list(csv.DictReader(stream))
stages = {stage['name']: stage for stage in native['stages']}
checks = {}
transitions = {}

def rows(name, start_offset=2, end_offset=None):
    stage = stages[name]
    start = stage['start_ms'] + start_offset * 1000
    end = stage['end_ms'] if end_offset is None else stage['start_ms'] + end_offset * 1000
    return [row for row in frames if start <= int(row['utc_ms']) <= end]

def check(name, selected, predicate):
    checks[name] = bool(selected) and all(predicate(row) for row in selected)

rendering = lambda row: row['paused'] == '0' and int(row['draw_calls'] or 0) > 10
check('active_30fps_rendering', rows('render_enabled'),
      lambda row: rendering(row) and row['target_fps'] == '30')
check('visible_idle_15fps_rendering', rows('visible_idle', 17),
      lambda row: rendering(row) and row['target_fps'] == '15')
for name in ('workarea_covered', 'fullscreen_covered'):
    predicate = lambda row: row['paused'] == '1' and row['target_fps'] == '15' and row['draw_calls'] != '' and int(row['draw_calls']) <= 2
    check(name + '_scene_stopped', rows(name), predicate)
    samples = [s for s in native['samples'] if stages[name]['start_ms'] <= s['utc_ms'] <= stages[name]['end_ms']]
    checks[name + '_owned_foreground'] = bool(samples) and all(s['foreground_owned'] for s in samples)
    matches = [r for r in rows(name, 0) if predicate(r)]
    transitions[name + '_first_paused_sample_ms'] = int(matches[0]['utc_ms']) - stages[name]['start_ms'] if matches else None
check('recovery_30fps_rendering', rows('recovered', 2, 10),
      lambda row: rendering(row) and row['target_fps'] == '30')
matches = [r for r in rows('recovered', 0) if rendering(r) and r['target_fps'] == '30']
transitions['recovery_first_render_sample_ms'] = int(matches[0]['utc_ms']) - stages['recovered']['start_ms'] if matches else None
for index in range(6):
    check('chat_' + str(index) + '_wakes_30fps', rows('chat_visible_' + str(index), .6),
          lambda row: rendering(row) and row['target_fps'] == '30')
chat = [a for a in native['actions'] if a['action'] == 'chat']
doors = [a for a in native['actions'] if a['action'] == 'door']
checks['six_native_chat_shows_under_300ms'] = len(chat) == 6 and all(a['show_ms'] < 300 for a in chat)
checks['twelve_door_commands_completed'] = len(doors) == 12
check('returns_to_idle_after_commands', rows('idle_after_commands', 17),
      lambda row: rendering(row) and row['target_fps'] == '15')
result = {
    'passed': all(checks.values()), 'checks': checks, 'transitions': transitions,
    'limits': 'Native window visibility is not first painted content or physical click latency. '
              'Transitions use sampled Unity frames and stage timestamps. '
              'Room panel navigation, target 12GB GPU with backend, and eight-hour stability are separate checks.'
}
(root / 'desktop-checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
raise SystemExit(0 if result['passed'] else 1)
