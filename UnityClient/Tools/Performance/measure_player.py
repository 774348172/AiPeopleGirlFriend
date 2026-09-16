"""Measure an owned release player and temporary cover window; never sends chat text.
Requires psutil. Uses PDH per-process WDDM dedicated/shared bytes, not total board memory.
"""
import argparse, ctypes as C, ctypes.wintypes as W, csv, json, os, re, socket, subprocess, time
from pathlib import Path
from urllib.request import Request, urlopen
import psutil

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('exe',type=Path);ap.add_argument('--output',required=True,type=Path)
ap.add_argument('--seconds',type=float,default=15)
ap.add_argument('--paused-only',action='store_true',help='Measure lock-screen residency only; no visible/interaction claims')
ap.add_argument('--graphics-api',choices=['d3d11','d3d12'])
args=ap.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
with socket.socket() as s:
    if s.connect_ex(('127.0.0.1',8771))==0: raise RuntimeError('Existing player owns port 8771; left untouched')

u=C.WinDLL('user32',use_last_error=True);pdh=C.WinDLL('pdh')
u.SetProcessDpiAwarenessContext.argtypes=[W.HANDLE];u.SetProcessDpiAwarenessContext(W.HANDLE(-4))
u.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p];u.CreateWindowExW.restype=W.HWND
u.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
u.SetForegroundWindow.argtypes=[W.HWND];u.ShowWindow.argtypes=[W.HWND,C.c_int];u.DestroyWindow.argtypes=[W.HWND]
u.GetForegroundWindow.restype=W.HWND
u.AttachThreadInput.argtypes=[W.DWORD,W.DWORD,W.BOOL]
u.BringWindowToTop.argtypes=[W.HWND];u.SetActiveWindow.argtypes=[W.HWND]
u.FindWindowExW.argtypes=[W.HWND,W.HWND,W.LPCWSTR,W.LPCWSTR];u.FindWindowExW.restype=W.HWND
u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
u.PeekMessageW.argtypes=[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT,W.UINT]
u.DispatchMessageW.argtypes=[C.POINTER(W.MSG)]
u.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
u.IsWindowVisible.argtypes=[W.HWND];u.IsWindowVisible.restype=W.BOOL
CB=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
u.EnumWindows.argtypes=[CB,W.LPARAM]
u.SystemParametersInfoW.argtypes=[W.UINT,W.UINT,C.c_void_p,W.UINT]

class Value(C.Structure): _fields_=[('status',W.DWORD),('value',C.c_double)]
class Item(C.Structure): _fields_=[('name',W.LPWSTR),('data',Value)]
pdh.PdhOpenQueryW.argtypes=[W.LPCWSTR,C.c_size_t,C.POINTER(W.HANDLE)]
pdh.PdhAddEnglishCounterW.argtypes=[W.HANDLE,W.LPCWSTR,C.c_size_t,C.POINTER(W.HANDLE)]
pdh.PdhCollectQueryData.argtypes=[W.HANDLE]
pdh.PdhGetFormattedCounterArrayW.argtypes=[W.HANDLE,W.DWORD,C.POINTER(W.DWORD),C.POINTER(W.DWORD),C.c_void_p]
pdh.PdhCloseQuery.argtypes=[W.HANDLE]
query=W.HANDLE();assert pdh.PdhOpenQueryW(None,0,C.byref(query))==0
counters={}
for name,leaf in [('dedicated','Dedicated Usage'),('shared','Shared Usage')]:
    counter=W.HANDLE();status=pdh.PdhAddEnglishCounterW(query,'\\GPU Process Memory(*)\\'+leaf,0,C.byref(counter))
    if status==0:counters[name]=counter

def gpu_memory(pids):
    pdh.PdhCollectQueryData(query);result={}
    for name,handle in counters.items():
        size=W.DWORD();count=W.DWORD()
        pdh.PdhGetFormattedCounterArrayW(handle,0x200,C.byref(size),C.byref(count),None)
        if not size.value:continue
        buf=C.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(handle,0x200,C.byref(size),C.byref(count),buf)!=0:continue
        items=C.cast(buf,C.POINTER(Item));total=0;matched=False
        for i in range(count.value):
            item=items[i];pid=re.search(r'pid_(\d+)_',item.name)
            if pid and int(pid[1]) in pids and item.data.status in (0,1):total+=item.data.value;matched=True
        result[name]=int(total) if matched else None
    return result

def request(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    with urlopen(Request('http://127.0.0.1:8771/ui/'+path,data=data,headers={'Content-Type':'application/json'}),timeout=2) as r:return json.load(r)

def pump():
    msg=W.MSG()
    while u.PeekMessageW(C.byref(msg),None,0,0,1):u.DispatchMessageW(C.byref(msg))

def foreground_owned():
    foreground=u.GetForegroundWindow()
    current=C.windll.kernel32.GetCurrentThreadId()
    other=u.GetWindowThreadProcessId(foreground,None)
    attached=current!=other and u.AttachThreadInput(current,other,True)
    try:
        u.ShowWindow(cover,5);u.BringWindowToTop(cover);u.SetActiveWindow(cover);u.SetForegroundWindow(cover)
    finally:
        if attached:u.AttachThreadInput(current,other,False)
    if u.GetForegroundWindow()!=cover:raise RuntimeError('Owned cover did not become foreground; measurement aborted')

def wait(check,timeout=30):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        pump()
        try:
            result=check()
            if result:return result
        except (OSError,ValueError):pass
        time.sleep(.02)
    raise TimeoutError('Player did not reach requested state')

def window_visible(pid):
    found=[]
    @CB
    def visit(h,_):
        p=W.DWORD();u.GetWindowThreadProcessId(h,C.byref(p))
        if p.value==pid and u.IsWindowVisible(h):found.append(h)
        return True
    u.EnumWindows(visit,0);return bool(found)

env=dict(os.environ,AIPEOPLE_PERF_OUTPUT=str(out))
process=subprocess.Popen([str(args.exe.resolve()),'-logFile',str(out/'player.log')]+(['-force-'+args.graphics_api] if args.graphics_api else []),cwd=args.exe.resolve().parent,env=env)
cover=None;stages=[];actions=[];samples=[];cpu_last={};chat_pid=None
def snapshot():
    parent=psutil.Process(process.pid);procs=[parent]+parent.children(recursive=True);now=time.monotonic()
    for proc in procs:
        try:
            if proc.name().lower()!='catgirlfriend.exe':continue
            m=proc.memory_info();cpu=proc.cpu_times();value=cpu.user+cpu.system;old=cpu_last.get(proc.pid)
            gpu=gpu_memory({proc.pid})
            samples.append(dict(utc_ms=int(time.time()*1000),pid=proc.pid,role='main' if proc.pid==process.pid else 'chat',
                rss=m.rss,private=getattr(m,'private',m.vms),handles=proc.num_handles(),
                cpu_core_percent=(value-old[1])/(now-old[0])*100 if old and now-old[0]>.05 else None,
                dedicated=gpu.get('dedicated'),shared=gpu.get('shared'),foreground_owned=u.GetForegroundWindow()==cover))
            cpu_last[proc.pid]=(now,value)
        except psutil.Error:pass

def stage(name,seconds=None):
    duration=args.seconds if seconds is None else seconds
    start=int(time.time()*1000);end=time.monotonic()+duration;next_sample=0
    while time.monotonic()<end:
        pump()
        if time.monotonic()>=next_sample:snapshot();next_sample=time.monotonic()+1
        time.sleep(.01)
    stages.append(dict(name=name,start_ms=start,end_ms=int(time.time()*1000)))
    print('Measured '+name,flush=True)

try:
    wait(lambda:request('room').get('doors'))
    parent=psutil.Process(process.pid)
    chat_pid=wait(lambda:next((p.pid for p in parent.children() if p.name().lower()=='catgirlfriend.exe'),None))
    tray=wait(lambda:u.FindWindowExW(W.HWND(-3),None,'STATIC','AiPeopleTray'))
    owner=W.DWORD();u.GetWindowThreadProcessId(tray,C.byref(owner));assert owner.value==process.pid
    if args.paused_only:
        stage('warmup',8)
        frames=list(out.glob('main-*-frames.csv'))
        latest=list(csv.DictReader(frames[0].open()))[-10:] if frames else []
        if not latest or any(r['paused']!='1' for r in latest):raise RuntimeError('Paused-only sample was rendering; no lock-screen result claimed')
        stage('locked_residency')
    else:
        # An owned foreground window controls the detector's native geometry without moving other apps.
        cover=u.CreateWindowExW(0x80,'STATIC','AiPeople performance measurement (temporary)',0x90000000,40,40,520,100,None,None,None,None)
        assert cover
        foreground_owned()
        stage('warmup',8)
        first=request('room')['doors'][0]
        request('door',{'id':first['id'],'state':'open'})
        stage('render_enabled',8);stage('visible_idle',max(args.seconds,20))
        sw=u.GetSystemMetrics(0);sh=u.GetSystemMetrics(1)
        work=W.RECT();assert u.SystemParametersInfoW(0x30,0,C.byref(work),0)
        u.SetWindowPos(cover,None,work.left,work.top,work.right-work.left,work.bottom-work.top,0x40);foreground_owned()
        stage('workarea_covered')
        u.SetWindowPos(cover,None,0,0,sw,sh,0x40);foreground_owned()
        stage('fullscreen_covered')
        u.SetWindowPos(cover,None,40,40,520,100,0x40);foreground_owned()
        stage('recovered')
        for i in range(6):
            start=time.perf_counter();assert u.PostMessageW(tray,0x8001,1,0x0202)
            wait(lambda:window_visible(chat_pid),3)
            shown=time.perf_counter()-start
            stage('chat_visible_'+str(i),2)
            start=time.perf_counter();request('hide',{});wait(lambda:not window_visible(chat_pid),3)
            actions.append(dict(action='chat',show_ms=shown*1000,hide_ms=(time.perf_counter()-start)*1000))
            foreground_owned()
        stage('door_commands',2)
        for door in request('room')['doors']:
            for state in ('closed','open'):
                start=time.perf_counter();result=request('door',{'id':door['id'],'state':state});ack=time.perf_counter()-start
                assert result['accepted'],result
                wait(lambda:next(d for d in request('room')['doors'] if d['id']==door['id'])['moving'] is False,3)
                actions.append(dict(action='door',id=door['id'],state=state,ack_ms=ack*1000,settled_ms=(time.perf_counter()-start)*1000))
                snapshot()
        stage('idle_after_commands')
finally:
    if cover:u.DestroyWindow(cover)
    if process.poll() is None:
        try:request('quit',{})
        except Exception:process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    pdh.PdhCloseQuery(query)
    (out/'native.json').write_text(json.dumps(dict(stages=stages,actions=actions,samples=samples,
        logical_cpus=psutil.cpu_count(),main_pid=process.pid,chat_pid=chat_pid),indent=2),encoding='utf-8')
