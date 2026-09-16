"""Read-only native attachment evidence for an explicitly supplied owned player PID."""
import argparse
import ctypes as C
import ctypes.wintypes as W
import json
import time
from pathlib import Path
import psutil

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('pid', type=int)
p.add_argument('output', type=Path)
args = p.parse_args()
proc = psutil.Process(args.pid)
assert proc.name().lower() == 'catgirlfriend.exe'
u = C.WinDLL('user32')
CB = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
u.EnumWindows.argtypes = [CB, W.LPARAM]
u.EnumChildWindows.argtypes = [W.HWND, CB, W.LPARAM]
u.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
u.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
u.GetParent.argtypes = [W.HWND]
u.GetParent.restype = W.HWND
u.GetWindow.argtypes = [W.HWND, W.UINT]
u.GetWindow.restype = W.HWND
u.GetWindowLongW.argtypes = [W.HWND, C.c_int]
u.GetWindowLongW.restype = C.c_long
u.FindWindowExW.argtypes = [W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR]
u.FindWindowExW.restype = W.HWND
u.GetForegroundWindow.restype = W.HWND
found = set()
children = {child.pid for child in proc.children() if child.name().lower() == 'catgirlfriend.exe'}
chat_windows = []
@CB
def visit(hwnd, _):
    pid = W.DWORD()
    u.GetWindowThreadProcessId(hwnd, C.byref(pid))
    name = C.create_unicode_buffer(128)
    u.GetClassNameW(hwnd, name, 128)
    if pid.value == args.pid and name.value == 'UnityWndClass':
        found.add(hwnd)
    if pid.value in children and name.value == 'UnityWndClass':
        chat_windows.append(dict(pid=pid.value, hwnd=hwnd, exstyle=hex(u.GetWindowLongW(hwnd, -20) & 0xffffffff)))
    return True
@CB
def top(hwnd, _):
    visit(hwnd, 0)
    u.EnumChildWindows(hwnd, visit, 0)
    return True
u.EnumWindows(top, 0)
assert len(found) == 1, found
hwnd = next(iter(found))
parent = u.GetParent(hwnd)
style = u.GetWindowLongW(hwnd, -16) & 0xffffffff
icon = u.FindWindowExW(parent, None, 'SHELLDLL_DefView', None) if parent else None
passed = bool(parent and style & 0x40000000 and not style & 0x00c00000
              and icon and u.GetWindow(hwnd, 3) == icon and u.GetForegroundWindow() != hwnd)
record = dict(utc_ms=int(time.time()*1000), pid=args.pid, exe=proc.exe(), hwnd=hwnd,
              parent=parent, style=hex(style), icon=icon, foreground=u.GetForegroundWindow(), passed=passed,
              chat_windows=chat_windows)
args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open('a', encoding='utf-8') as stream:
    stream.write(json.dumps(record) + '\n')
print(json.dumps(record))
raise SystemExit(0 if passed else 1)
