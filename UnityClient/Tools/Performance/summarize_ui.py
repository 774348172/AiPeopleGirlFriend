"""Summarize real UI milestone captures (Unity frame/GPU completion, not monitor scanout)."""
import argparse
import csv
import json
import statistics
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('folder', type=Path)
args = p.parse_args()
root = args.folder
def load(role):
    files = list(root.glob(role + '-*-ui.csv'))
    if len(files) != 1:
        raise RuntimeError('One process capture per role required in a fresh directory: ' + role)
    with files[0].open() as stream:
        return list(csv.DictReader(stream))
chat, main = load('chat'), load('main')
metrics = {}
def pairs(name, starts, end_label, clock='monotonic_ms', limit=3000):
    values = []
    for end in chat:
        if end['stage'] != end_label:
            continue
        earlier = [s for s in starts if 0 <= float(end[clock]) - float(s[clock]) < limit]
        if earlier:
            values.append(round(float(end[clock]) - float(earlier[-1][clock]), 3))
    metrics[name] = {'samples_ms': values, 'count': len(values),
                     'median_ms': statistics.median(values) if values else None,
                     'max_ms': max(values) if values else None}
def starts(label):
    return [r for r in chat if r['stage'] == label]
hotkeys = [r for r in main if r['stage'] == 'hotkey_received']
for end in ('native_shown', 'show_end_frame', 'history_end_frame', 'history_gpu_ready'):
    # Unity Mono Stopwatch origins differ across processes; use UTC only across roles.
    pairs('hotkey_to_' + end, hotkeys, end, clock='utc_ms')
pairs('close_release_to_hidden', starts('close_pointer_up'), 'native_hidden')
for end in ('room_open_end_frame', 'room_ready_end_frame', 'room_close_end_frame'):
    pairs('room_release_to_' + end, starts('room_pointer_up'), end)
for end in ('door_ack', 'door_ack_end_frame', 'door_settled_ui_end_frame'):
    door_starts = [r for r in chat if r['stage'].startswith('door_') and r['stage'].endswith('_pointer_up')]
    pairs('door_release_to_' + end, door_starts, end)
report = {'metrics': metrics,
          'limits': 'Actual OS input via Windows automation, followed by Unity pointer dispatch milestones. '
                    'End-frame means Unity finished frame submission; GPU readback proves a rendered image, '
                    'not physical monitor scanout. Optional screenshot copies/PNG encoding add diagnostic overhead. '
                    'Cross-process deltas use UTC (1ms resolution); intra-process uses Stopwatch. '
                    'Automation discoverability may expose the chat in the taskbar; record launch flags separately.'}
(root / 'ui-summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
for name, value in metrics.items():
    print(name, value)
