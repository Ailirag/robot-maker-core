"""Explain a recorded minimum wall chord; never accept or hide the finding."""
import hashlib,math
from pathlib import Path

TOLERANCE_MM=2e-5
NORMAL_ANGLE_DEG=15.

def identity():
    from engineering import weakness_scan as W
    return dict(context_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                geometric_runtime=W.identity())

def inspect(shape,finding):
    from build123d import Face,Vector
    from OCP.gp import gp_Lin,gp_Pnt,gp_Dir
    from engineering.weakness_scan import _Rays
    def refuse(why):raise ValueError("CHORD_CONTEXT: "+why)
    if not isinstance(finding,dict) or finding.get("kind")!="THIN_WALL":
        refuse("a thin-wall finding is required")
    points=finding.get("chord_mm");length=finding.get("min_thickness_mm")
    if (not isinstance(points,(list,tuple)) or len(points)!=2 or
        any(not isinstance(p,(list,tuple)) or len(p)!=3 or
            any(type(v) not in (int,float) or not math.isfinite(v) for v in p) for p in points) or
        type(length) not in (int,float) or not math.isfinite(length) or length<=0):
        refuse("finite coordinates and positive thickness required")
    if not shape.is_valid or len(shape.solids())!=1:refuse("valid single body required")
    a,b=map(Vector,points);delta=b-a;distance=delta.length
    if distance<=TOLERANCE_MM or abs(distance-length)>TOLERANCE_MM:
        refuse("recorded length differs from endpoints")
    direction=delta.normalized();rays=_Rays(shape.solids()[0])
    rays.engine.Perform(gp_Lin(gp_Pnt(*a),gp_Dir(*direction)),-TOLERANCE_MM,distance+TOLERANCE_MM)
    if not rays.engine.IsDone() or rays.engine.NbPnt()!=2:
        refuse("endpoints do not select exactly two boundary intersections")
    hits=sorted((rays.engine.WParameter(i),i) for i in range(1,3))
    if abs(hits[0][0])>TOLERANCE_MM or abs(hits[1][0]-distance)>TOLERANCE_MM:
        refuse("reported endpoints are not current boundaries")
    if not rays.inside(tuple((a+b)*.5)):refuse("chord crosses air")
    normals=[];types=[]
    for _,i in hits:
        face=Face(rays.engine.Face(i));point=Vector(rays.engine.Pnt(i))
        normal=face.normal_at(point)
        normals.append(min(1.,abs(normal.dot(direction))))
        types.append(str(face.geom_type).removeprefix("GeomType."))
    threshold=math.cos(math.radians(NORMAL_ANGLE_DEG))
    if normals[0]<threshold:refuse("entry does not follow the surface normal")
    return dict(schema="wall-chord-context-v1",finding_id=finding.get("id"),
        chord_mm=[list(a),list(b)],verified_length_mm=hits[1][0]-hits[0][0],
        entry_surface=types[0],exit_surface=types[1],
        entry_normal_dot_chord=normals[0],exit_normal_dot_chord=normals[1],
        exit_angle_deg=math.degrees(math.acos(normals[1])),
        context="OPPOSING_SURFACES" if normals[1]>=threshold else "OBLIQUE_EXIT",
        angle_threshold_deg=NORMAL_ANGLE_DEG,tolerance_mm=TOLERANCE_MM,
        scope="reported minimum chord only",requires_engineering_review=True,
        physical_approval=False,strength_n=None,
        obtained_by="Review neighbouring sections, actual load paths and all samples; qualify material and fasteners separately.",
        limits="An oblique exit may still be a weak wedge or attachment. This context neither accepts nor removes the finding and says nothing about other samples grouped into it.")
