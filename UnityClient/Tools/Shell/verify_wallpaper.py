"""Windows player smoke test: own tray callback, chat visibility, wallpaper parent/styles.
Only launches the supplied preview executable; refuses an occupied UI port.
No mouse/keyboard injection, desktop icon changes, or messages to other applications.
"""
import argparse, ctypes as C, ctypes.wintypes as W, json, socket, subprocess, time
from pathlib import Path
from urllib.request import Request, urlopen

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('exe',type=Path)
parser.add_argument('--output', type=Path, help='Keep results for a new build separate from earlier regressions')
parser.add_argument('--doors', action='store_true', help='Also exercise the real room controls while mounted as wallpaper')
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
out=args.output.resolve() if args.output else root/'output/wallpaper-click-fix'
out.mkdir(parents=True,exist_ok=True)
with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1',8771))==0:raise RuntimeError('Another player is running; stop it before testing')
u=C.WinDLL('user32',use_last_error=True)
CB=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
u.EnumWindows.argtypes=[CB,W.LPARAM];u.EnumChildWindows.argtypes=[W.HWND,CB,W.LPARAM]
u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
u.GetClassNameW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
u.GetParent.argtypes=[W.HWND];u.GetParent.restype=W.HWND
u.GetWindow.argtypes=[W.HWND,C.c_uint];u.GetWindow.restype=W.HWND
u.GetWindowLongW.argtypes=[W.HWND,C.c_int];u.GetWindowLongW.restype=C.c_long
u.FindWindowExW.argtypes=[W.HWND,W.HWND,W.LPCWSTR,W.LPCWSTR];u.FindWindowExW.restype=W.HWND
u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];u.PostMessageW.restype=W.BOOL

def owner(hwnd):
    pid=W.DWORD();u.GetWindowThreadProcessId(hwnd,C.byref(pid));return pid.value

def cls(hwnd):
    text=C.create_unicode_buffer(256);u.GetClassNameW(hwnd,text,256);return text.value

def window(pid):
    found=[]
    @CB
    def visit(hwnd,_):
        if owner(hwnd)==pid and cls(hwnd)=='UnityWndClass':found.append(hwnd)
        return True
    @CB
    def top(hwnd,_):
        visit(hwnd,0);u.EnumChildWindows(hwnd,visit,0);return True
    u.EnumWindows(top,0)
    return found[0] if found else None

def wait(predicate,timeout=20):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=predicate()
        if result:return result
        time.sleep(.1)
    raise TimeoutError('Player did not reach expected state')

def request(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    with urlopen(Request('http://127.0.0.1:8771/ui/'+path,data=data,
                         headers={'Content-Type':'application/json'}),timeout=2) as response:
        return json.load(response)

def wallpaper(pid):
    h=window(pid)
    if not h:return None
    parent=u.GetParent(h);style=u.GetWindowLongW(h,-16)&0xffffffff
    if not parent or not(style&0x40000000) or style&0x00c00000:return None
    icon=u.FindWindowExW(parent,None,'SHELLDLL_DefView',None)
    if not icon or u.GetWindow(h,3)!=icon:return None
    return {'hwnd':h,'parent':parent,'style':hex(style),'above':cls(icon)}

observations=[]
door_observations=[]
for run in range(2):
    process=subprocess.Popen([str(args.exe.resolve()),'-logFile',str(out/f'player-run-{run+1}.log')],
                             cwd=args.exe.resolve().parent)
    try:
        initial=wait(lambda:wallpaper(process.pid),30)
        wait(lambda:u.FindWindowExW(W.HWND(-3),None,'STATIC','AiPeopleTray'))
        tray=u.FindWindowExW(W.HWND(-3),None,'STATIC','AiPeopleTray')
        assert owner(tray)==process.pid,'Tray belongs to another process'
        time.sleep(.3)
        if args.doors and run == 0:
            doors = request('room')['doors']
            assert len(doors) == 6, 'Expected the six installed room controls'
            for door in doors:
                for state in ['closed', 'open']:
                    started = time.monotonic()
                    result = request('door', {'id': door['id'], 'state': state})
                    acknowledged = time.monotonic() - started
                    assert result['accepted'], result
                    assert request('door', {'id': door['id'], 'state': state})['accepted']
                    def settled():
                        current = next(d for d in request('room')['doors'] if d['id'] == door['id'])
                        return current if not current['moving'] and current['open'] == (state == 'open') else None
                    current = wait(settled, 5)
                    attached = wallpaper(process.pid)
                    assert attached and attached['hwnd'] == initial['hwnd'] and attached['parent'] == initial['parent']
                    door_observations.append({'id': door['id'], 'state': state, 'ack_seconds': acknowledged,
                                              'settled_seconds': time.monotonic()-started, 'snapshot': current})
            print('Six room controls closed and reopened without leaving the wallpaper layer', flush=True)
        for cycle in range(3):
            # The exact notification consumed by production TrayIcon.WndProc for a left click.
            assert u.PostMessageW(tray,0x8001,1,0x0202)
            wait(lambda:request('visibility').get('show') is True)
            shown=wallpaper(process.pid);assert shown and shown['hwnd']==initial['hwnd'] and shown['parent']==initial['parent']
            request('hide',{})
            wait(lambda:request('visibility').get('show') is False)
            request('mode',{'mode':'wallpaper'})
            time.sleep(.7)
            recovered=wallpaper(process.pid)
            assert recovered and recovered['hwnd']==initial['hwnd'] and recovered['parent']==initial['parent']
            observations.append({'run':run+1,'cycle':cycle+1,'after_chat':shown,'after_restore':recovered})
        cfg=Path.home()/'AppData/LocalLow/AiPeople/CatGirlfriend/aipeople_shell.json'
        assert json.loads(cfg.read_text(encoding='utf-8-sig'))['windowMode']==1
        print(f'Run {run+1}: three tray/chat/recovery cycles kept wallpaper attachment',flush=True)
    finally:
        if process.poll() is None:
            try:request('quit',{})
            except Exception:process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        time.sleep(.5)
(out/'native-results.json').write_text(json.dumps({'passed':True,'observations':observations,
    'door_observations':door_observations},indent=2)+'\n',encoding='utf-8')
