"""Open measured internal doors and a balcony sliding panel, preserving their UVs.

Run after repair_kitchen_door.py. Always reads that stage's unmodified output;
rendering and collision receive exactly the same cuts and transforms.
These measurements apply only to the September 14 shell at its original calibration.
"""
from pathlib import Path
import json
import struct
import numpy as np

FOLDER = Path(__file__).resolve().parents[2] / 'output/apartment-import'
SCALE = 13.888656
OFFSET = np.array([.0002448312, .5031348, -.0061515938])
DOORS = [
    dict(name='MasterDoor', pivot=[-.43, 0, .84], direction=[.83205, 0, .5547],
         limits=[[.015, 1.07], [.085, 1.70], [-.35, .20]], yaw=-56.31, shift=[0, 0, 0]),
    dict(name='SecondaryDoor', pivot=[4.69, 0, .46], direction=[-.447214, 0, .894427],
         limits=[[.015, 1.10], [.085, 1.70], [-.20, .23]], yaw=26.565, shift=[0, 0, 0]),
    dict(name='BathroomDoor', pivot=[4.48, 0, -1.64], direction=[-.707107, 0, -.707107],
         limits=[[.015, 1.13], [.085, 1.70], [-.20, .23]], yaw=-45, shift=[0, 0, 0]),
    dict(name='HallDoor', pivot=[2.87, 0, -.06], direction=[0, 0, -1],
         limits=[[0, 1.18], [.085, 1.85], [-.22, .22]], yaw=90, shift=[0, 0, 0]),
    dict(name='BalconyPanel', pivot=[-1.27, 0, -4.84], direction=[1, 0, 0],
         limits=[[0, 1.64], [.085, 1.85], [-.24, .24]], yaw=0, shift=[1.64, 0, -.12]),
]
CUTS = [dict(name='MasterOpening', pivot=[-.42, 0, .58], direction=[1, 0, 0],
             limits=[[0, 1.10], [.035, 2.0], [-.3, .3]])]


def triangles(poly):
    for i in range(1, len(poly)-1):
        tri = np.array([poly[0], poly[i], poly[i+1]])
        if np.linalg.norm(np.cross(tri[1, :3]-tri[0, :3], tri[2, :3]-tri[0, :3])) > 1e-9:
            yield tri


def split(faces, spec):
    pivot = np.array(spec['pivot'])
    direction = np.array(spec['direction'])
    basis = np.column_stack((direction, [0, 1, 0], np.cross(direction, [0, 1, 0])))
    outside, part = [], []
    for tri in faces:
        local = (tri[:, :3]-pivot) @ basis
        if any(local[:, axis].max() < lo or local[:, axis].min() > hi
               for axis, (lo, hi) in enumerate(spec['limits'])):
            outside.append(tri)
            continue
        remaining = list(tri)
        for axis, (lo, hi) in enumerate(spec['limits']):
            for value, sign in [(lo, 1), (hi, -1)]:
                inside, discarded = [], []
                distances = [sign * (((p[:3]-pivot) @ basis)[axis]-value) for p in remaining]
                for i, p in enumerate(remaining):
                    j = (i+1) % len(remaining)
                    q, a, b = remaining[j], distances[i], distances[j]
                    (inside if a >= 0 else discarded).append(p)
                    if (a >= 0) != (b >= 0):
                        cut = p+(q-p)*(a/(a-b))
                        inside.append(cut)
                        discarded.append(cut)
                outside.extend(triangles(discarded))
                remaining = inside
                if not remaining:
                    break
            if not remaining:
                break
        part.extend(triangles(remaining))
    if len(part) < 10:
        raise RuntimeError(f"Empty door selection: {spec['name']}")
    return outside, part


def save(name, faces, pivot=None):
    v = np.asarray(faces).reshape(-1, 5).copy()
    if pivot is not None:
        v[:, :3] -= pivot
    else:
        w = (v[:, :3]-OFFSET)/SCALE
        v[:, :3] = np.column_stack((w[:, 2], w[:, 1], -w[:, 0]))
    unique, indices = np.unique(v.astype('<f4'), axis=0, return_inverse=True)
    with (FOLDER/name).open('wb') as file:
        file.write(struct.pack('<ii', len(unique), len(indices)))
        unique.tofile(file)
        indices.astype('<i4').tofile(file)
    return len(indices)//3


def reduce_shell(faces, target):
    # Recover the small triangulation overhead of clipping without moving portal boundaries.
    import pymeshlab as ml
    flat = np.asarray(faces).reshape(-1, 5)
    unique, indices = np.unique(flat, axis=0, return_inverse=True)
    meshes = ml.MeshSet()
    meshes.add_mesh(ml.Mesh(vertex_matrix=unique[:, :3], face_matrix=indices.reshape(-1, 3),
                           v_tex_coords_matrix=unique[:, 3:]))
    meshes.compute_texcoord_transfer_vertex_to_wedge()
    meshes.meshing_remove_duplicate_vertices()
    meshes.meshing_decimation_quadric_edge_collapse_with_texture(
        targetfacenum=target, preservenormal=True, preserveboundary=True, extratcoordw=10.0)
    mesh = meshes.current_mesh()
    return np.column_stack((mesh.vertex_matrix()[mesh.face_matrix()].reshape(-1, 3),
                            mesh.wedge_tex_coord_matrix())).reshape(-1, 3, 5)


def main():
    report = dict(doors=DOORS)
    for kind in ['render', 'collision']:
        with (FOLDER/f'repaired-{kind}-mesh.bin').open('rb') as file:
            nv, ni = struct.unpack('<ii', file.read(8))
            v = np.fromfile(file, '<f4', nv*5).reshape(-1, 5).astype(float)
            indices = np.fromfile(file, '<i4', ni).reshape(-1, 3)
        v[:, :3] = np.column_stack((-v[:, 2], v[:, 1], v[:, 0]))*SCALE+OFFSET
        shell = list(v[indices])
        stats = {}
        for spec in DOORS:
            shell, part = split(shell, spec)
            stats[spec['name']] = save(f"{spec['name']}-{kind}.bin", part, np.array(spec['pivot']))
        for spec in CUTS:
            shell, _ = split(shell, spec)
        kitchen = json.loads((FOLDER/'kitchen-door-repair.json').read_text())[kind]['door_triangles']
        if kind == 'render':
            shell = reduce_shell(shell, 15000-sum(stats.values())-kitchen-60)
        stats['shell'] = save(f'walkable-{kind}-mesh.bin', shell)
        stats['sills_and_jamb'] = 60 if kind == 'render' else 0  # BoxColliders do not use triangle meshes.
        stats['total_triangles'] = sum(stats.values())+kitchen
        report[kind] = stats
        print(kind, stats, flush=True)
        if kind == 'render' and stats['total_triangles'] > 15300:
            raise RuntimeError('Room repairs exceeded the approximate 15k budget')
    (FOLDER/'room-passages.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
