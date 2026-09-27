"""Measure thin concentric cylindrical walls directly from exact B-Rep faces.

Screening covers axis-aligned cylinders, including horizontal print placement.
It does not find every wall or prove the strength of a joint.
"""
from __future__ import annotations
import math
from build123d import Vector
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder

def annular_necks(shape,*,min_wall_mm):
    if not math.isfinite(min_wall_mm) or min_wall_mm<=0:raise ValueError("Positive screening wall required")
    if not shape.is_valid:raise ValueError("Invalid B-Rep for wall screening")
    faces=[]
    for face in shape.faces():
        surface=BRepAdaptor_Surface(face.wrapped)
        if surface.GetType()!=GeomAbs_Cylinder:continue
        cylinder=surface.Cylinder();axis=cylinder.Axis();origin=axis.Location();d=axis.Direction()
        direction=[d.X(),d.Y(),d.Z()];major=max(range(3),key=lambda i:abs(direction[i]))
        if abs(direction[major])<1-1e-8:continue
        center=[origin.X(),origin.Y(),origin.Z()];point=surface.Value(
            (surface.FirstUParameter()+surface.LastUParameter())/2,
            (surface.FirstVParameter()+surface.LastVParameter())/2)
        position=Vector(point.X(),point.Y(),point.Z());normal=face.normal_at(position)
        radial=[position.X-center[0],position.Y-center[1],position.Z-center[2]];radial[major]=0.
        sign=sum(tuple(normal)[i]*radial[i] for i in range(3))
        bounds=face.bounding_box()
        faces.append(dict(axis=major,center=center,radius=cylinder.Radius(),
            outer=sign>0,lo=list(bounds.min)[major],hi=list(bounds.max)[major]))
    found={}
    for outer in faces:
        if not outer["outer"]:continue
        for inner in faces:
            if inner["outer"] or inner["axis"]!=outer["axis"]:continue
            k=outer["axis"]
            if any(abs(outer["center"][i]-inner["center"][i])>1e-5 for i in range(3) if i!=k):continue
            wall=outer["radius"]-inner["radius"];lo=max(outer["lo"],inner["lo"]);hi=min(outer["hi"],inner["hi"])
            if not 1e-6<wall<min_wall_mm or hi-lo<.001:continue
            center=list(outer["center"]);center[k]=(lo+hi)/2
            row=dict(axis="XYZ"[k],center_mm=[round(x,6) for x in center],
                outer_diameter_mm=round(2*outer["radius"],6),bore_diameter_mm=round(2*inner["radius"],6),
                wall_mm=round(wall,6),axial_range_mm=[round(lo,6),round(hi,6)],
                screening_limit_mm=min_wall_mm,provenance="CALCULATED_FROM_BREP",
                strength=None,obtained_by="Qualified structural analysis or physical load/torque acceptance of this joint.")
            found[tuple(center)+(wall,lo,hi)]=row
    rows=[found[k] for k in sorted(found)]
    for i,r in enumerate(rows):r["id"]=f"neck-{i+1}"
    return rows

def match_parts(step_path,meshes,*,min_wall_mm):
    from build123d import import_step
    bodies=list(import_step(step_path).solids());result={}
    if len(bodies)!=len(meshes):raise ValueError("PRINTABILITY: STEP part count differs")
    for mesh in meshes:
        lo=[min(v[k] for v in mesh["vertices"]) for k in range(3)]
        hi=[max(v[k] for v in mesh["vertices"]) for k in range(3)]
        matched=[s for s in bodies if all(abs(a-b)<.081 for a,b in
            zip(list(s.bounding_box().min)+list(s.bounding_box().max),lo+hi))]
        if len(matched)!=1:raise ValueError("PRINTABILITY: ambiguous STEP/mesh body mapping")
        shape=matched[0];bodies.remove(shape)
        result[mesh["name"]]=annular_necks(shape,min_wall_mm=min_wall_mm)
    return result
