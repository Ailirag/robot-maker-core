"""Nominal screw length must not hide an air gap or a partly supported seat."""
import pytest
from build123d import Box, Pos, Rot

def pair():
    return Pos(0,0,1)*Box(10,10,2),Pos(0,0,-1)*Box(10,10,2)

def scan(a,b,**kw):
    from engineering import bearing_support as B
    return B.scan(a,b,origin_mm=[0,0,0],axis=2,span_mm=[6,6],hole_radius_mm=1,**kw)

def test_touching_seats_are_geometrically_supported_but_not_strength_approved():
    r=scan(*pair())
    assert r["status"]=="SUPPORTED_GEOMETRICALLY"
    assert r["physical_approval"] is False and r["strength_n"] is None

def test_valid_blind_screw_length_cannot_hide_a_gap():
    a,b=pair();r=scan(Pos(0,0,.3)*a,b)
    assert r["status"]=="MISSING_BEARING"
    assert r["positive"]["missing_mm3"]>0
    assert r["negative"]["status"]=="PASS"

def test_partial_contact_is_not_accepted_as_full_contact():
    a,b=pair();r=scan(a,Pos(4,0,0)*b)
    assert r["status"]=="MISSING_BEARING"

def test_plane_can_be_transformed_to_another_axis():
    from engineering import bearing_support as B
    a,b=pair()
    r=B.scan(Rot(0,90,0)*a,Rot(0,90,0)*b,origin_mm=[0,0,0],axis=0,span_mm=[6,6],hole_radius_mm=1)
    assert r["status"]=="SUPPORTED_GEOMETRICALLY"

@pytest.mark.parametrize("kwargs",[{"axis":True},{"span_mm":[2,2]},{"origin_mm":[0,float("nan"),0]},{"probe_depth_mm":0}])
def test_invalid_probe_is_refused(kwargs):
    from engineering import bearing_support as B
    args=dict(origin_mm=[0,0,0],axis=2,span_mm=[6,6],hole_radius_mm=1);args.update(kwargs)
    with pytest.raises(ValueError,match="bearing"):
        B.scan(*pair(),**args)
