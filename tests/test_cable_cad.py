import math
import pytest
from engineering import cable_routing as R, cable_cad as C

def test_brep_matches_analytic_length_and_tube_volume():
    route=R.fillet([[0,0,0],[20,0,0],[20,20,0]],5)
    wire=C.wire(route)
    assert wire.length == pytest.approx(30+2.5*math.pi,abs=1e-7)
    tube=C.envelope(route,2)
    assert tube.is_valid and len(tube.solids())==1
    assert tube.volume == pytest.approx(math.pi*wire.length,rel=1e-6)

def test_brep_collisions_use_actual_solid_not_bounding_box():
    from build123d import Box,Pos
    route=R.fillet([[0,0,0],[20,0,0]],3)
    tube=C.envelope(route,2)
    assert C.collisions(tube,{"block":Pos(10,0,0)*Box(1,1,1)})
    assert not C.collisions(tube,{"clear":Pos(10,5,0)*Box(1,1,1)})

def test_thick_tube_cannot_hide_impossible_bend():
    route=R.fillet([[0,0,0],[20,0,0],[20,20,0]],2)
    with pytest.raises(ValueError,match="BEND"):C.envelope(route,5)

