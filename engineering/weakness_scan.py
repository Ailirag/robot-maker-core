"""Geometry-driven wall and attachment screening, independent of feature names.

B-Rep surface-normal chords detect thin flat/curved walls. A bounded voxel
erosion detects thick regions connected only through narrow material. Neither
method estimates material strength or establishes that every defect is absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from engineering.structural_scan import _solid, _bounds, _round, _finding

WALL_LIMIT_MM = 1.5
SURFACE_PITCH_MM = 1.0
VOXEL_PITCH_MM = 0.5
EROSION_RADIUS_MM = 1.5
MIN_CORE_MM3 = 8.0
# Stream surface probes; reserve dense arrays only within the explicit cell cap.
# A 200 mm enclosure needs over 20 million cells at the unchanged 0.5 mm pitch.
MAX_SURFACE_SAMPLES = 2000000
MAX_VOXELS = 32000000
MESH_TOLERANCE_MM = 0.03
TOL = 1e-6


def identity():
    import build123d
    import OCP
    import numpy
    import scipy
    from engineering import structural_scan
    return dict(weakness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                helpers_sha256=structural_scan.identity()["analyzer_sha256"],
                build123d=build123d.__version__, ocp=OCP.__version__,
                numpy=numpy.__version__, scipy=scipy.__version__)


def _pitch(value, maximum):
    if type(value) not in (int,float) or not math.isfinite(value) or not .1 <= value <= maximum:
        raise ValueError("weakness: sampling pitch must be finite, at least 0.1 mm and no coarser than policy")
    return float(value)


class _Rays:
    """Reuse the exact intersector; never measure an air gap as material."""
    def __init__(self, shape):
        from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        self.shape = shape
        self.engine = IntCurvesFace_ShapeIntersector()
        self.engine.Load(shape.wrapped, TOL/10)
        self.classifier = BRepClass3d_SolidClassifier(shape.wrapped)

    def inside(self, point):
        from OCP.gp import gp_Pnt
        from OCP.TopAbs import TopAbs_IN
        self.classifier.Perform(gp_Pnt(*point),TOL/10)
        return self.classifier.State() == TopAbs_IN

    def crossings(self, point, direction, low, high):
        from OCP.gp import gp_Lin, gp_Pnt, gp_Dir
        self.engine.Perform(gp_Lin(gp_Pnt(*point),gp_Dir(*direction)),low,high)
        if not self.engine.IsDone():
            raise ValueError("weakness: B-Rep ray intersection failed")
        values = sorted(self.engine.WParameter(i) for i in range(1,self.engine.NbPnt()+1))
        result = []
        for value in values:
            if not math.isfinite(value):
                raise ValueError("weakness: non-finite ray intersection")
            if not result or value-result[-1] > TOL:
                result.append(value)
        return result

    def intervals(self, point, direction, low, high):
        values = self.crossings(point,direction,low,high)
        result = []
        for a,b in zip(values,values[1:]):
            mid = tuple(p+d*(a+b)/2 for p,d in zip(point,direction))
            if self.inside(mid):
                result.append((a,b))
        return result


def _triangles(face, pitch):
    """Longest-edge subdivision bounds chord-space sampling, including tiny faces."""
    from copy import deepcopy
    from OCP.BRepTools import BRepTools
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    mesh_face = deepcopy(face)
    BRepTools.Clean_s(mesh_face.wrapped)
    mesher = BRepMesh_IncrementalMesh(mesh_face.wrapped,MESH_TOLERANCE_MM,False,.15,False)
    if not mesher.IsDone():
        raise ValueError("weakness: face triangulation failed")
    vertices, triangles = mesh_face.tessellate(MESH_TOLERANCE_MM, .15)
    if not triangles:
        raise ValueError("weakness: face has no sampling triangles")
    pending = [tuple(vertices[i] for i in ids) for ids in triangles]
    while pending:
        a,b,c = pending.pop()
        pairs = [((a-b).length,a,b,c),((b-c).length,b,c,a),((c-a).length,c,a,b)]
        length,a,b,c = max(pairs,key=lambda x:x[0])
        if length > pitch:
            mid = (a+b)/2
            pending.extend(((a,mid,c),(mid,b,c)))
        else:
            yield (a+b+c)/3


def _surface_budget(value):
    value=MAX_SURFACE_SAMPLES if value is None else value
    if type(value) is not int or value<=0:
        raise ValueError("weakness: surface budget must be a positive integer")
    return value


def scan_walls(shape, *, surface_pitch_mm=SURFACE_PITCH_MM, max_surface_samples=None):
    from build123d import Vector
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    _solid(shape)
    pitch = _pitch(surface_pitch_mm,SURFACE_PITCH_MM)
    budget = _surface_budget(max_surface_samples)
    rays = _Rays(shape)
    diagonal = shape.bounding_box().diagonal + 1
    findings, total, minimum, skipped = [], 0, None, 0
    faces = list(shape.faces())
    for index,face in enumerate(faces):
        hits, samples, face_min, pair = [], 0, None, None
        for seed in _triangles(face,pitch):
            total += 1
            if total > budget:
                raise ValueError("weakness: surface sampling budget exceeded")
            projection = GeomAPI_ProjectPointOnSurf(seed.to_pnt(),face.geom_adaptor())
            if projection.NbPoints() == 0 or projection.LowerDistance() > MESH_TOLERANCE_MM*2:
                raise ValueError("weakness: surface projection outside tessellation tolerance")
            point = Vector(projection.NearestPoint())
            direction = -face.normal_at(point)
            values = rays.crossings(tuple(point),tuple(direction),-TOL*10,diagonal)
            positive = [v for v in values if v>TOL]
            if not any(abs(v)<TOL*10 for v in values) or not positive:
                # A tessellation chord at a curved trim can project just
                # outside it. Trust the complete B-Rep boundary intersection;
                # isolated periodic-face classifiers can misclassify valid UVs.
                if face.distance_to(point) <= MESH_TOLERANCE_MM*2:
                    skipped += 1
                    continue
                raise ValueError(f"weakness: unresolved inward surface chord at face {index}, {point}")
            samples += 1
            thickness = positive[0]
            if not rays.inside(point+direction*(thickness/2)):
                raise ValueError("weakness: surface chord is not inside material")
            minimum = thickness if minimum is None else min(minimum,thickness)
            if face_min is None or thickness < face_min:
                face_min, pair = thickness, [_round(point),_round(point+direction*thickness)]
            if thickness < WALL_LIMIT_MM-TOL:
                hits.append(tuple(point))
        if not samples:
            raise ValueError("weakness: face has no verified interior samples")
        if hits:
            bounds = [[min(p[i] for p in hits) for i in range(3)],
                      [max(p[i] for p in hits) for i in range(3)]]
            findings.append(_finding(dict(kind="THIN_WALL",face=index,
                min_thickness_mm=round(face_min,6), chord_mm=pair,
                bounds_mm=[_round(v) for v in bounds],face_bounds_mm=[_round(v) for v in _bounds(face)],
                thin_samples=len(hits),samples=samples)))
    return dict(schema="surface-thickness-scan-v1",status="REVIEW_REQUIRED" if findings else "SCREENED",
        findings=sorted(findings,key=lambda f:f["id"]),sampled_faces=len(faces),
        sampled_points=total-skipped,trim_boundary_samples_skipped=skipped,
        minimum_measured_mm=None if minimum is None else round(minimum,6),
        coverage=dict(complete=True,meaning="Every face sampled; not every surface point"),
        policy=dict(wall_limit_mm=WALL_LIMIT_MM,max_surface_pitch_mm=pitch,
                    mesh_tolerance_mm=MESH_TOLERANCE_MM,max_samples=budget),
        limitation="Surface-normal material chords at samples; tiny features between samples and off-normal minima may escape.")


def _voxel_budget(value):
    value=MAX_VOXELS if value is None else value
    if type(value) is not int or value<=0:
        raise ValueError("weakness: voxel budget must be a positive integer")
    return value


def _voxelize(shape, pitch, *, max_voxels=None):
    import numpy as np
    bounds = _bounds(shape)
    origin = np.array(bounds[0])-pitch
    counts = np.ceil((np.array(bounds[1])-np.array(bounds[0]))/pitch).astype(int)+2
    cells = math.prod(int(n) for n in counts)
    if cells > _voxel_budget(max_voxels):
        raise ValueError("weakness: voxel sampling budget exceeded")
    occupied = np.zeros(tuple(counts),dtype=bool)
    axis = int(np.argmax(counts))
    other = [i for i in range(3) if i!=axis]
    direction = [0.,0.,0.]
    direction[axis] = 1.
    positions = origin[axis]+(np.arange(counts[axis])+.5)*pitch
    rays = _Rays(shape)
    for u in range(counts[other[0]]):
        for v in range(counts[other[1]]):
            point = origin.copy()
            point[axis] = 0.
            point[other[0]] += (u+.5)*pitch
            point[other[1]] += (v+.5)*pitch
            intervals = rays.intervals(point,direction,origin[axis]-pitch,positions[-1]+pitch)
            line = np.zeros(counts[axis],dtype=bool)
            for a,b in intervals:
                line |= (positions>a+TOL)&(positions<b-TOL)
            key = [slice(None)]*3
            key[other[0]],key[other[1]] = u,v
            occupied[tuple(key)] = line
    return occupied,origin


def _region(mask, origin, pitch):
    import numpy as np
    positions = np.argwhere(mask)
    return dict(bounds_mm=[_round(origin+positions.min(axis=0)*pitch),
                           _round(origin+(positions.max(axis=0)+1)*pitch)],
                volume_estimate_mm3=round(len(positions)*pitch**3,6))


def scan_necks(shape, *, voxel_pitch_mm=VOXEL_PITCH_MM, max_voxels=None):
    import numpy as np
    from scipy import ndimage as nd
    _solid(shape)
    pitch = _pitch(voxel_pitch_mm,VOXEL_PITCH_MM)
    budget = _voxel_budget(max_voxels)
    occupied,origin = _voxelize(shape,pitch,max_voxels=budget)
    connected = nd.generate_binary_structure(3,1)
    # Oblique boundaries leave cells touching along edges/corners. The B-Rep
    # already proved one solid; 26-neighbour occupancy avoids artificial splits.
    _,pieces = nd.label(occupied,nd.generate_binary_structure(3,3))
    complete = pieces==1
    findings = []
    if not complete:
        findings.append(_finding(dict(kind="GRID_UNRESOLVED",bounds_mm=[_round(v) for v in _bounds(shape)],
            sampled_components=int(pieces),reason="Valid single B-Rep was lost or split by sampling; refine pitch")))
    distance = nd.distance_transform_edt(occupied,sampling=pitch)
    # The distance is to empty cell centres, not an exact B-Rep thickness.
    core = distance > EROSION_RADIUS_MM+pitch/2
    labels,count = nd.label(core,connected)
    sizes = np.bincount(labels.ravel())
    keep = [i for i in range(1,count+1) if sizes[i]*pitch**3 >= MIN_CORE_MM3]
    regions = [_region(labels==i,origin,pitch) for i in keep]
    if complete and len(keep)>1:
        seeds = np.isin(labels,keep)
        # Locate candidate separating corridors by nearest surviving thick region.
        indices = nd.distance_transform_edt(~seeds,return_distances=False,return_indices=True)
        owners = labels[tuple(indices)]
        boundary = np.zeros_like(occupied)
        for axis in range(3):
            a,b = [slice(None)]*3,[slice(None)]*3
            a[axis],b[axis] = slice(None,-1),slice(1,None)
            a,b=tuple(a),tuple(b)
            changed = (owners[a]!=owners[b]) & occupied[a] & occupied[b]
            boundary[a] |= changed
            boundary[b] |= changed
        corridors,n = nd.label(boundary,nd.generate_binary_structure(3,3))
        for i in range(1,n+1):
            mask = corridors==i
            region = _region(mask,origin,pitch)
            findings.append(_finding(dict(kind="NARROW_CONNECTION",**region,
                separated_core_count=len(keep),
                core_regions=regions,
                sampled_clearance_mm=round(float(distance[mask].max()),6),
                method="voxel_erosion_and_nearest_core_boundary")))
        if not n:
            raise ValueError("weakness: disconnected erosion cores without a located corridor")
    return dict(schema="connection-neck-scan-v1",status="REVIEW_REQUIRED" if findings else "SCREENED",
        findings=sorted(findings,key=lambda f:f["id"]),
        coverage=dict(complete=complete,meaning="Connected solid rasterized at declared pitch"),
        grid=dict(origin_mm=_round(origin),shape=list(occupied.shape),pitch_mm=pitch,
                  cells=int(occupied.size),occupied_cells=int(occupied.sum()),
                  volume_estimate_mm3=round(float(occupied.sum())*pitch**3,6)),
        surviving_core_count=len(keep),discarded_small_cores=count-len(keep),
        policy=dict(erosion_radius_mm=EROSION_RADIUS_MM,min_core_mm3=MIN_CORE_MM3,max_voxels=budget),
        limitation="Voxel erosion finds geometric bottlenecks between thick cores, not semantic joints or load paths. Thin appendages without a thick core rely on wall screening; long bending arms and stress concentrations need load review.")


def scan(shape, *, surface_pitch_mm=SURFACE_PITCH_MM, voxel_pitch_mm=VOXEL_PITCH_MM, max_voxels=None, max_surface_samples=None):
    walls = scan_walls(shape,surface_pitch_mm=surface_pitch_mm,max_surface_samples=max_surface_samples)
    necks = scan_necks(shape,voxel_pitch_mm=voxel_pitch_mm,max_voxels=max_voxels)
    findings = sorted(walls["findings"]+necks["findings"],key=lambda f:f["id"])
    return dict(schema="geometry-weakness-scan-v1",identity=identity(),
        status="REVIEW_REQUIRED" if findings else "SCREENED",
        coverage=dict(complete=walls["coverage"]["complete"] and necks["coverage"]["complete"]),
        findings=findings,walls=walls,connections=necks,
        strength_n=None,obtained_by="Qualified load cases, printed material/process evidence and physical tests.",
        physical_approval=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step",type=Path,required=True)
    parser.add_argument("--part",help="Exact top-level body label for multi-body STEP")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--surface-pitch",type=float,default=SURFACE_PITCH_MM)
    parser.add_argument("--voxel-pitch",type=float,default=VOXEL_PITCH_MM)
    parser.add_argument("--max-voxels",type=int,default=None,help="Explicit memory budget; never changes sampling pitch")
    parser.add_argument("--max-surface-samples",type=int,default=None,help="Explicit surface work budget; never changes sampling pitch")
    args=parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    from build123d import import_step
    shape=import_step(args.step)
    if args.part:
        matches=[s for s in shape.children if s.label==args.part]
        if len(matches)!=1:
            raise ValueError("weakness: missing or ambiguous STEP body label")
        shape=matches[0]
    report=scan(shape,surface_pitch_mm=args.surface_pitch,voxel_pitch_mm=args.voxel_pitch,max_voxels=args.max_voxels,max_surface_samples=args.max_surface_samples)
    report["step_sha256"]=hashlib.sha256(args.step.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8",newline="\n") as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write("\n")
    print(report["status"]+": "+str(len(report["findings"]))+" findings; "+str(args.output))
    return 2 if report["findings"] or not report["coverage"]["complete"] else 0


if __name__=="__main__":
    raise SystemExit(main())
