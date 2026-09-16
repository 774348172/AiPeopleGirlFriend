"""Two owned release launches, read-only wallpaper inspection and local door API smoke checks.

Does not synthesize keyboard/mouse/tray input or communicate with the backend.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('exe', type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)

def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    with urlopen(Request('http://127.0.0.1:8771/ui/' + path, data=data,
                         headers={'Content-Type': 'application/json'}), timeout=2) as response:
        return json.load(response)

def wait_ready(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError):
            pass
        time.sleep(.1)
    raise TimeoutError('Owned furnished player did not reach expected state')

def attachment(pid):
    check = subprocess.run([sys.executable, str(root/'Tools/Performance/record_attachment.py'),
                            str(pid), str(output/'attachment.jsonl')],
                           capture_output=True, text=True, check=True)
    return json.loads(check.stdout)

observations = []
for run in range(2):
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 8771)) == 0:
            raise RuntimeError('Existing player left untouched; UI port is occupied')
    environment = os.environ.copy()
    environment['AIPEOPLE_PERF_OUTPUT'] = str(output/f'run-{run+1}')
    process = subprocess.Popen([str(args.exe.resolve()), '-logFile', str(output/f'player-{run+1}.log')],
                               cwd=args.exe.resolve().parent, env=environment)
    try:
        wait_ready(lambda: request('health'))
        time.sleep(1)
        initial = attachment(process.pid)
        doors = request('room')['doors']
        assert len(doors) == 6
        events = []
        if run == 0:
            for door in doors:
                for state in ('closed', 'open'):
                    started = time.monotonic()
                    assert request('door', {'id': door['id'], 'state': state})['accepted']
                    def settled():
                        current = next(d for d in request('room')['doors'] if d['id'] == door['id'])
                        return current if not current['moving'] and current['open'] == (state == 'open') else None
                    current = wait_ready(settled, 6)
                    events.append(dict(id=door['id'], state=state,
                                       seconds=time.monotonic()-started, snapshot=current))
        time.sleep(3)
        final = attachment(process.pid)
        assert final['hwnd'] == initial['hwnd'] and final['parent'] == initial['parent']
        observations.append(dict(run=run+1, pid=process.pid, initial=initial, final=final, doors=events))
        print(f'Launch {run+1}: wallpaper attachment preserved; {len(events)} door transitions', flush=True)
    finally:
        if process.poll() is None:
            try:
                request('quit', {})
                process.wait(timeout=10)
            except Exception:
                process.terminate()
                process.wait(timeout=10)
        time.sleep(1)

(output/'results.json').write_text(json.dumps(dict(passed=True, observations=observations), indent=2)+'\n', encoding='utf-8')
