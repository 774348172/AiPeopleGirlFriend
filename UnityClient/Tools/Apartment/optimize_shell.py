"""Offline shell reduction. Requires numpy and pymeshlab==2025.7.post1.
Input: source-mesh.bin exported by ApartmentShellImporter.Inspect.
Output: compact Unity-ready buffers; original FBX is never modified.
"""
from pathlib import Path
import numpy as np
import pymeshlab as ml
import struct
import argparse
import json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--render-triangles", type=int, default=15000)
parser.add_argument("--skip-collision", action="store_true", help="Keep the verified static collision mesh")
args = parser.parse_args()
if args.render_triangles < 1000:
    parser.error("render-triangles must be at least 1000")

root = Path(__file__).resolve().parents[2]
folder = root / 'output/apartment-import'
with (folder / 'source-mesh.bin').open('rb') as f:
    nv, ni = struct.unpack('<ii', f.read(8))
    data = np.fromfile(f, dtype='<f4', count=nv*5).reshape(-1,5)
    faces = np.fromfile(f, dtype='<i4', count=ni).reshape(-1,3)

def write(name, v, uv, faces):
    with (folder/name).open('wb') as f:
        f.write(struct.pack('<ii', len(v), faces.size))
        np.column_stack((v,uv)).astype('<f4').tofile(f)
        faces.astype('<i4').tofile(f)
    print(name, 'vertices',len(v),'triangles',len(faces), flush=True)

ms=ml.MeshSet()
ms.add_mesh(ml.Mesh(vertex_matrix=data[:,:3].astype(float), face_matrix=faces,
                    v_tex_coords_matrix=data[:,3:].astype(float)))
ms.compute_texcoord_transfer_vertex_to_wedge()
ms.meshing_remove_duplicate_vertices()
ms.meshing_decimation_quadric_edge_collapse_with_texture(targetfacenum=args.render_triangles, preservenormal=True, preserveboundary=False, extratcoordw=10.0)
m=ms.current_mesh()
flat=np.column_stack((m.vertex_matrix()[m.face_matrix()].reshape(-1,3),m.wedge_tex_coord_matrix()))
unique,indices=np.unique(flat.astype('<f4'),axis=0,return_inverse=True)
actual = indices.size // 3
if actual > args.render_triangles * 1.02:
    raise RuntimeError(f'Decimation did not meet budget: {actual} triangles')
write('render-mesh.bin',unique[:,:3],unique[:,3:],indices.reshape(-1,3))
(folder/'optimization-stats.json').write_text(json.dumps({
    'source_triangles':len(faces), 'target_triangles':args.render_triangles,
    'render_triangles':actual, 'render_vertices':len(unique),
    'source_bounds_min':data[:,:3].min(axis=0).tolist(),
    'source_bounds_max':data[:,:3].max(axis=0).tolist(),
    'render_bounds_min':unique[:,:3].min(axis=0).tolist(),
    'render_bounds_max':unique[:,:3].max(axis=0).tolist(),
},indent=2)+'\n',encoding='utf-8')
if args.skip_collision:
    raise SystemExit(0)
ms=ml.MeshSet()
ms.add_mesh(ml.Mesh(vertex_matrix=data[:,:3].astype(float),face_matrix=faces))
ms.meshing_remove_duplicate_vertices()
ms.meshing_decimation_quadric_edge_collapse(targetfacenum=8000, preservenormal=True,preserveboundary=True)
m=ms.current_mesh()
write('collision-mesh.bin',m.vertex_matrix(),np.zeros((m.vertex_number(),2)),m.face_matrix())
