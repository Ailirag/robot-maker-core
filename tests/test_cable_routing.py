"""Analytical cable geometry and fail-closed fabrication decisions."""
import math
import pytest
from engineering import cable_routing as C

def known(value):
    return {"value": value, "obtained_by": "fixture measurement"}

def recipe(points=None):
    return dict(id="wire", points=known(points or [[0,0,0],[20,0,0]]),
        diameter_mm=known(2), bend_radius_mm=known(3), clearance_mm=known(1),
        termination_mm=known([2,3]), service_mm=known(5),
        available_mm=known(100), endpoints_confirmed=True, obstacles_complete=True)

def test_quarter_circle_has_exact_length_and_tangent_endpoints():
    route=C.fillet([[0,0,0],[20,0,0],[20,20,0]],5)
    assert route["length_mm"] == pytest.approx(30+math.pi*2.5)
    assert route["primitives"][1]["start"] == pytest.approx([15,0,0])
    assert route["primitives"][1]["end"] == pytest.approx([20,5,0])
    assert route["primitives"][1]["kind"] == "arc"

def test_spatial_corner_and_redundant_collinear_points():
    route=C.fillet([[0,0,0],[10,0,0],[20,0,0],[20,0,20]],5)
    assert route["length_mm"] == pytest.approx(30+math.pi*2.5)

@pytest.mark.parametrize("points,radius", [
    ([[0,0,0],[4,0,0],[4,4,0],[8,4,0]],3),
    ([[0,0,0],[20,0,0],[0,0,0]],1),
    ([[0,0,0],[0,0,0]],1),
    ([[0,0,0],[float("nan"),0,0]],1),
    ([[0,0,0],[10,0,0]],0)])
def test_invalid_or_overlapping_bends_refused(points,radius):
    with pytest.raises(ValueError): C.fillet(points,radius)

def test_collision_crosses_thin_obstacle_between_samples():
    obstacle=dict(id="thin",bounds=[[10.001,-1,-1],[10.002,1,1]])
    r=C.assess(recipe(),[obstacle])
    assert r["status"] == "BLOCKED"
    assert "COLLISION:thin" in r["issues"]
    assert r["cut_length_mm"] is None

def test_diameter_and_clearance_inflate_obstacle():
    obstacle=dict(id="wall",bounds=[[5,1.9,-1],[10,4,1]])
    assert C.assess(recipe(),[obstacle])["status"] == "BLOCKED"

def test_arc_collision_not_only_polyline_collision():
    # Bend cuts the corner and crosses (18.5355,1.4645).
    r=recipe([[0,0,0],[20,0,0],[20,20,0]])
    r.update(bend_radius_mm=known(5),diameter_mm=known(.01),clearance_mm=known(0))
    obstacle=dict(id="corner",bounds=[[18.52,1.45,-.1],[18.55,1.48,.1]])
    assert "COLLISION:corner" in C.assess(r,[obstacle])["issues"]

def test_cut_length_is_centerline_plus_terminations_and_service():
    r=C.assess(recipe(),[])
    assert r["route_length_mm"] == 20
    assert r["cut_length_mm"] == 30
    assert r["status"] == "CALCULATED"
    rcp=recipe();rcp["available_mm"]=known(29)
    assert C.assess(rcp,[])["status"] == "BLOCKED"

@pytest.mark.parametrize("key",["points","diameter_mm","bend_radius_mm","clearance_mm","termination_mm","service_mm"])
def test_unknown_is_not_a_zero_or_fabrication_length(key):
    r=recipe();r[key]=known(None)
    out=C.assess(r,[])
    assert out["cut_length_mm"] is None
    assert out["status"] == "INCOMPLETE"
    assert out["unknowns"][key]["value"] is None

def test_planned_endpoints_and_partial_obstacles_never_qualify():
    r=recipe();r.update(endpoints_confirmed=False,obstacles_complete=False)
    out=C.assess(r,[])
    assert out["route_length_mm"] == 20
    assert out["cut_length_mm"] is None
    assert out["status"] == "INCOMPLETE"

def test_unknowns_need_collection_procedure_and_nonfinite_rejected():
    r=recipe();r["service_mm"]={"value":None}
    with pytest.raises(ValueError,match="obtained_by"): C.assess(r,[])
    r=recipe();r["service_mm"]=known(float("inf"))
    with pytest.raises(ValueError): C.assess(r,[])

def test_inventory_requires_every_conductor_exactly_once():
    a=recipe();b=dict(recipe([[0,10,0],[20,10,0]]),id="second")
    assert not C.inventory([a,b],["wire","second"],[])["issues"]
    assert "MISSING:second" in C.inventory([a],["wire","second"],[])["issues"]
    with pytest.raises(ValueError,match="DUPLICATE"): C.inventory([a,a],["wire"],[])

def test_solver_finds_detour_and_reports_bounded_failure():
    obstacles=[dict(id="block",bounds=[[8,-3,-3],[12,3,3]])]
    route=C.search([0,0,0],[20,0,0],obstacles,
        bounds=[[-2,-10,-2],[22,10,2]],step=2,margin=1,max_nodes=10000)
    assert route is not None and len(route)>2
    assert not C.polyline_collisions(route,obstacles,1)
    assert C.search([0,0,0],[20,0,0],obstacles,
        bounds=[[-2,-1,-1],[22,1,1]],step=2,margin=1,max_nodes=500) is None
