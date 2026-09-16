"""Summarize raw release-player frames and process samples; retain unavailable readings as null."""
import argparse,csv,json,math,statistics
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);args=ap.parse_args();root=args.folder
n=json.loads((root/'native.json').read_text(encoding='utf-8'))
def percentile(values,q):
    values=sorted(values)
    if not values:return None
    index=(len(values)-1)*q;lo=math.floor(index);hi=math.ceil(index)
    return values[lo]+(values[hi]-values[lo])*(index-lo)
def stats(values):
    values=[float(v) for v in values if v not in ('',None)]
    return {'count':len(values),'p50':percentile(values,.5),'p95':percentile(values,.95),'max':max(values) if values else None}
frames={}
for role in ('main','chat'):
    files=list(root.glob(role+'-*-frames.csv'))
    frames[role]=list(csv.DictReader(files[0].open())) if files else []
report={'note':'CPU frame time includes pacing waits; cpu_main/cpu_render describe measured thread work. GPU timings when no draws are not an active-render benchmark. CPU percent is one logical core; divide by logical_cpus for whole-machine percent. PDH dedicated/shared bytes are per process, not total board usage.','logical_cpus':n['logical_cpus'],'stages':[],'actions':n['actions']}
for stage in n['stages']:
    if stage['name']=='warmup':continue
    start=stage['start_ms']+min(2000,(stage['end_ms']-stage['start_ms'])/4)
    entry={'name':stage['name'],'duration_seconds':(stage['end_ms']-stage['start_ms'])/1000}
    for role in ('main','chat'):
        f=[r for r in frames[role] if start<float(r['utc_ms'])<stage['end_ms']]
        p=[r for r in n['samples'] if r['role']==role and start<r['utc_ms']<stage['end_ms']]
        entry[role]={k:stats(r[k] for r in f) for k in ('frame_ms','cpu_main_ms','cpu_render_ms','gpu_ms','draw_calls','triangles','target_fps')}
        entry[role]['paused_fraction']=statistics.mean(float(r['paused']) for r in f) if f else None
        entry[role]['process']={k:stats(r.get(k) for r in p) for k in ('rss','private','dedicated','shared','cpu_core_percent','handles')}
    report['stages'].append(entry)
(root/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
for s in report['stages']:
    p=s['main']['process'];c=s['chat']['process']
    def mb(role,key):
        v=role[key]['p50'];return round(v/1048576,1) if v is not None else None
    print(s['name'], 'pause=',s['main']['paused_fraction'],'GPU p95=',s['main']['gpu_ms']['p95'],
          'main/chat RSS MiB=',mb(p,'rss'),mb(c,'rss'),'dedicated MiB=',mb(p,'dedicated'),mb(c,'dedicated'))
