"""Check declared opposing bearing footprints without mistaking an air gap for a seat.

Geometry of the expected footprint is an explicit design input. This is not a
search for undeclared joints, a screw-stack calculation or a strength rating.
"""
import math
from engineering.joint_review import material_coverage

def scan(positive,negative,*,origin_mm,axis,span_mm,hole_radius_mm=0.,probe_depth_mm=.01):
    from build123d import Box,Cylinder,Align,Pos,Rot
    if (type(axis) is not int or axis not in (0,1,2) or
            not isinstance(origin_mm,(list,tuple)) or len(origin_mm)!=3 or
            not isinstance(span_mm,(list,tuple)) or len(span_mm)!=2):
        raise ValueError("bearing: axis, origin and two transverse spans required")
    numbers=(*origin_mm,*span_mm,hole_radius_mm,probe_depth_mm)
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in numbers):
        raise ValueError("bearing: finite geometry required")
    if min(span_mm)<=0 or not 0<=hole_radius_mm<min(span_mm)/2 or not 1e-4<=probe_depth_mm<=1.:
        raise ValueError("bearing: nonempty footprint and bounded probe depth required")
    transverse=[i for i in range(3) if i!=axis]
    def probe(side):
        sizes=[0.,0.,0.];low=list(origin_mm)
        sizes[axis]=probe_depth_mm
        if side<0:low[axis]-=probe_depth_mm
        for i,span in zip(transverse,span_mm):
            sizes[i]=span;low[i]-=span/2
        shape=Pos(*low)*Box(*sizes,align=(Align.MIN,)*3)
        if hole_radius_mm:
            start=list(origin_mm);start[axis]-=2*probe_depth_mm
            rotate=Rot(0,90,0) if axis==0 else Rot(-90,0,0) if axis==1 else Rot(0,0,0)
            shape-=Pos(*start)*rotate*Cylinder(hole_radius_mm,4*probe_depth_mm,align=(Align.CENTER,Align.CENTER,Align.MIN))
        return shape
    pos=material_coverage(positive,probe(1));neg=material_coverage(negative,probe(-1))
    return dict(schema="planar-bearing-support-v1",
        status="SUPPORTED_GEOMETRICALLY" if pos["status"]==neg["status"]=="PASS" else "MISSING_BEARING",
        positive=pos,negative=neg,origin_mm=list(origin_mm),axis=axis,
        span_mm=list(span_mm),hole_radius_mm=hole_radius_mm,probe_depth_mm=probe_depth_mm,
        limitation="Declared nominal bearing footprint only; finite probe depth and the material-coverage tolerance apply. Print tolerance, insert seating, fastener torque, friction and physical contact/load are not qualified.",
        physical_approval=False,strength_n=None,
        obtained_by="Qualified material, fastening process, tolerances and physical fit/load evidence remain required.")
