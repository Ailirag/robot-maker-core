import pytest
from engineering import cable_routing as C
def v(n):return {"value":n,"obtained_by":"fixture"}
def wire(points,r=2,d=1):
    return dict(id="loop",points=v(points),diameter_mm=v(d),bend_radius_mm=v(r),
        clearance_mm=v(0),termination_mm=v([0,0]),service_mm=v(0),available_mm=v(None),
        endpoints_confirmed=True,obstacles_complete=True)
def test_self_crossing_centerline_is_not_a_cut_instruction():
    r=C.assess(wire([[0,0,0],[20,0,0],[20,20,0],[10,20,0],[10,-10,0]]),[])
    assert "SELF_INTERFERENCE" in r["issues"] and r["cut_length_mm"] is None
def test_cross_section_cannot_be_bigger_than_bend():
    r=C.assess(wire([[0,0,0],[20,0,0],[20,20,0]],r=1,d=4),[])
    assert "BEND_TOO_TIGHT_FOR_DIAMETER" in r["issues"]
def test_empty_harness_is_not_a_complete_job():
    with pytest.raises(ValueError,match="EMPTY"):C.inventory([],[],[])

