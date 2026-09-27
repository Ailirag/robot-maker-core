"""Build123d authors the same tangent arcs used by the cable-length solver."""
from __future__ import annotations
import math
from build123d import Edge, Wire, Plane, Circle, Rectangle, sweep
from engineering import cable_path as R

def wire(route):
    edges=[]
    for s in route["primitives"]:
        if s["kind"]=="line":edges.append(Edge.make_line(s["start"],s["end"]))
        else:
            angle=s["angle_rad"]/2
            mid=R.add(s["center"],R.mul(R.add(R.mul(s["axis_u"],math.cos(angle)),
                R.mul(s["axis_v"],math.sin(angle))),s["radius_mm"]))
            edges.append(Edge.make_three_point_arc(s["start"],mid,s["end"]))
    wires=Wire.combine(edges)
    if len(wires)!=1:raise ValueError("DISCONNECTED_CABLE")
    result=wires[0]
    if abs(result.length-route["length_mm"])>1e-6:raise ValueError("CABLE_LENGTH_MISMATCH")
    return result

def envelope(route,diameter_mm):
    diameter=R.number(diameter_mm,positive=True)
    if any(s["kind"]=="arc" and s["radius_mm"]<=diameter/2 for s in route["primitives"]):
        raise ValueError("BEND_TOO_TIGHT_FOR_ENVELOPE")
    path=wire(route)
    s=route["primitives"][0]
    tangent=R.unit(R.sub(s["end"],s["start"])) if s["kind"]=="line" else s["axis_v"]
    section=Plane(origin=s["start"],z_dir=tangent)*Circle(diameter/2)
    result=sweep(section,path)
    if not result.is_valid or len(result.solids())!=1:raise ValueError("CABLE_BREP_INVALID")
    expected=math.pi*(diameter/2)**2*route["length_mm"]
    if abs(result.volume-expected)>max(1e-5,expected*1e-5):raise ValueError("CABLE_SELF_INTERSECTION")
    return result

def collisions(shape,bodies,tolerance_mm3=1e-6):
    result=[]
    for name,body in bodies.items():
        common=shape.intersect(body)
        volume=0 if common is None else sum(s.volume for s in common.solids())
        if volume>tolerance_mm3:result.append(dict(body=name,volume_mm3=volume))
    return result

def ribbon_envelope(route,*,width_mm,thickness_mm,width_axis):
    """Flatwise planar ribbon; reject twist or an unspecified moving frame.

    A space curve is not automatically a feasible FFC. Its width direction
    must be perpendicular to every tangent; otherwise an explicit twist model
    and cable-specific limits are needed before fabrication.
    """
    width=R.number(width_mm,positive=True);thickness=R.number(thickness_mm,positive=True)
    axis=R.unit(R.point(width_axis))
    for s in route["primitives"]:
        tangents=[R.unit(R.sub(s["end"],s["start"]))] if s["kind"]=="line" else [s["axis_u"],s["axis_v"]]
        if any(abs(R.dot(axis,t))>1e-7 for t in tangents):raise ValueError("RIBBON_ORIENTATION_OR_TWIST")
        if s["kind"]=="arc" and s["radius_mm"]<=thickness/2:raise ValueError("RIBBON_BEND")
    first=route["primitives"][0]
    tangent=R.unit(R.sub(first["end"],first["start"])) if first["kind"]=="line" else first["axis_v"]
    section=Plane(origin=first["start"],x_dir=axis,z_dir=tangent)*Rectangle(width,thickness)
    result=sweep(section,wire(route),binormal=axis)
    expected=route["length_mm"]*width*thickness
    if not result.is_valid or len(result.solids())!=1 or abs(result.volume-expected)>max(1e-5,expected*1e-5):
        raise ValueError("RIBBON_BREP_OR_SELF_INTERSECTION")
    return result
