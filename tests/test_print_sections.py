import pytest
from build123d import Cylinder,Box,Pos,Align,Compound
from engineering.print_sections import annular_necks

def tube(ro,ri,height=2):
    return Cylinder(ro,height,align=(Align.CENTER,Align.CENTER,Align.MIN))-Cylinder(ri,height,align=(Align.CENTER,Align.CENTER,Align.MIN))

def test_thin_counterbore_neck_is_measured_from_opposed_cylindrical_faces():
    r=annular_necks(tube(4,3.25),min_wall_mm=.84)
    assert len(r)==1
    assert r[0]["wall_mm"]==pytest.approx(.75)
    assert r[0]["outer_diameter_mm"]==pytest.approx(8)
    assert r[0]["bore_diameter_mm"]==pytest.approx(6.5)

def test_thick_tube_and_plain_hole_are_not_thin_annular_necks():
    assert not annular_necks(tube(4,1.7),min_wall_mm=.84)
    block=Box(10,10,2)-Cylinder(3.25,4)
    assert not annular_necks(block,min_wall_mm=.84)

def test_counterbores_at_different_heights_are_not_a_wall():
    a=Cylinder(4,2,align=(Align.CENTER,Align.CENTER,Align.MIN))
    b=Pos(0,0,3)*tube(6,3.25)
    assert not annular_necks(Compound(children=[a,b]),min_wall_mm=.84)

def test_rotated_axis_aligned_neck_is_still_detected():
    from build123d import Axis
    assert len(annular_necks(tube(4,3.25).rotate(Axis.X,90),min_wall_mm=.84))==1

def test_neck_findings_require_a_specific_engineering_disposition():
    from engineering.print_risks import verify_review
    from test_print_risks import report,review
    r=report();v=review()
    r["parts"][0]["annular_necks"]=[{"id":"neck-1","wall_mm":.75}]
    with pytest.raises(ValueError,match="PRINTABILITY"):verify_review(r,v)
    v["parts"]["bracket"]["annular_necks"]={"neck-1":{"reason":"Counterbore leaves a thin structural root; physical strength not established.","strength":None,"obtained_by":"Physical load/torque test or qualified structural analysis."}}
    assert verify_review(r,v)["physical_approval"] is False
