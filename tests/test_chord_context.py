"""A short edge chord must stay visible without being called a parallel wall."""
import copy
import pytest
from build123d import Align,Box,Face,Wire,extrude
from engineering import chord_context as C

def finding(a,b,length):
    return dict(id="probe",kind="THIN_WALL",chord_mm=[a,b],min_thickness_mm=length)

def box():
    return Box(10,10,1,align=(Align.MIN,)*3)

def test_opposing_faces_are_reported_without_approval():
    f=finding([5,5,0],[5,5,1],1)
    before=copy.deepcopy(f)
    r=C.inspect(box(),f)
    assert f==before
    assert r["context"]=="OPPOSING_SURFACES"
    assert r["exit_normal_dot_chord"]==pytest.approx(1)
    assert r["requires_engineering_review"] is True
    assert r["physical_approval"] is False
    assert r["scope"]=="reported minimum chord only"

def test_edge_wedge_is_identified_but_not_accepted():
    wedge=extrude(Face(Wire.make_polygon([(0,0,0),(10,0,0),(0,10,0)],close=True)),amount=10)
    r=C.inspect(wedge,finding([9.5,0,5],[9.5,.5,5],.5))
    assert r["context"]=="OBLIQUE_EXIT"
    assert r["exit_normal_dot_chord"]==pytest.approx(2**-.5)
    assert r["exit_angle_deg"]==pytest.approx(45)
    assert r["requires_engineering_review"] is True

@pytest.mark.parametrize("row",[
    finding([5,5,0],[5,5,1],.4),
    finding([15,5,0],[15,5,1],1),
    finding([5,5,.2],[5,5,1.2],1),
    finding([5,5,0],[5,5,0],0),
    finding([5,5,float("nan")],[5,5,1],1),
    dict(id="p",kind="SMALL_SECTION",chord_mm=[[5,5,0],[5,5,1]],min_thickness_mm=1),
])
def test_stale_or_unsupported_chords_are_refused(row):
    with pytest.raises(ValueError,match="CHORD_CONTEXT"):
        C.inspect(box(),row)

def test_air_gap_is_never_measured_as_material():
    from build123d import Pos
    hollow=Box(10,10,3,align=(Align.MIN,)*3)-Pos(0,0,1)*Box(9,10,1,align=(Align.MIN,)*3)
    with pytest.raises(ValueError,match="CHORD_CONTEXT"):
        C.inspect(hollow,finding([5,5,1],[5,5,2],1))
