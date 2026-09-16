"""Split the measured kitchen door from the 15k shell; preserve UVs on both parts.
Run after optimize_shell.py and before ApartmentShellImporter.Install.
Coordinates below are the calibrated September 14 apartment, in metres.
"""
from pathlib import Path
import struct,json
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
FOLDER=ROOT/'output/apartment-import'
SCALE=13.888656
OFFSET=np.array([.0002448312,.5031348,-.0061515938])
PIVOT=np.array([-4.18,0.,1.25])
D=np.sqrt(.5)
BASIS=np.array([[D,0,-D],[0,1,0],[D,0,D]])
LIMITS=[(0,.015,1.13),(1,.085,1.60),(2,-.28,.17)]

def clip(poly,axis,value,sign):
    inside=[];outside=[]
    if len(poly)==0:return inside,outside
    distances=[sign*(((p[:3]-PIVOT)@BASIS)[axis]-value) for p in poly]
    for i,p in enumerate(poly):
        j=(i+1)%len(poly);q=poly[j];a=distances[i];b=distances[j]
        (inside if a>=0 else outside).append(p)
        if (a>=0)!=(b>=0):
            cut=p+(q-p)*(a/(a-b));inside.append(cut);outside.append(cut)
    return inside,outside

def triangles(poly):
    for i in range(1,len(poly)-1):
        t=np.array([poly[0],poly[i],poly[i+1]])
        if np.linalg.norm(np.cross(t[1,:3]-t[0,:3],t[2,:3]-t[0,:3]))>1e-10:yield t

def save(path,faces,door=False):
    v=np.asarray(faces).reshape(-1,5).copy()
    if door:v[:,:3]-=PIVOT
    else:
        world=(v[:,:3]-OFFSET)/SCALE
        v[:,:3]=np.column_stack((world[:,2],world[:,1],-world[:,0]))
    unique,indices=np.unique(v.astype('<f4'),axis=0,return_inverse=True)
    with path.open('wb') as f:
        f.write(struct.pack('<ii',len(unique),len(indices)));unique.tofile(f);indices.astype('<i4').tofile(f)
    return len(indices)//3

report={}
for kind in ['render','collision']:
    with (FOLDER/f'{kind}-mesh.bin').open('rb') as f:
        nv,ni=struct.unpack('<ii',f.read(8));v=np.fromfile(f,dtype='<f4',count=nv*5).reshape(-1,5).astype(float)
        indices=np.fromfile(f,dtype='<i4',count=ni).reshape(-1,3)
    v[:,:3]=np.column_stack((-v[:,2],v[:,1],v[:,0]))*SCALE+OFFSET
    shell=[];door=[]
    for tri in v[indices]:
        local=(tri[:,:3]-PIVOT)@BASIS
        if any(local[:,axis].max()<low or local[:,axis].min()>high for axis,low,high in LIMITS):
            shell.append(tri)
            continue
        remaining=list(tri)
        for axis,low,high in LIMITS:
            for value,sign in [(low,1),(high,-1)]:
                remaining,out=clip(remaining,axis,value,sign)
                shell.extend(triangles(out))
                if not remaining:break
            if not remaining:break
        if remaining:door.extend(triangles(remaining))
    if len(door)<10:raise RuntimeError(f'{kind}: door selection failed')
    a=save(FOLDER/f'repaired-{kind}-mesh.bin',shell)
    b=save(FOLDER/f'kitchen-door-{kind}-mesh.bin',door,True)
    report[kind]={'shell_triangles':a,'door_triangles':b,'total_triangles':a+b}
    print(kind,report[kind],flush=True)
    if kind=='render' and a+b>15300:raise RuntimeError('Split exceeded 15k rendering budget tolerance')
report['scale']=SCALE;report['offset']=OFFSET.tolist();report['door_pivot']=PIVOT.tolist();report['open_yaw']=-45
(FOLDER/'kitchen-door-repair.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
