import pytest
from engineering import cable_cad as C, cable_routing as R
def test_planar_ribbon_keeps_width_axis_and_exact_volume():
    route=R.fillet([[0,0,0],[20,0,0],[20,20,0]],5)
    shape=C.ribbon_envelope(route,width_mm=8,thickness_mm=.2,width_axis=[0,0,1])
    assert shape.is_valid
    assert shape.volume==pytest.approx(route["length_mm"]*8*.2,rel=1e-6)
def test_unmodelled_twist_is_refused():
    route=R.fillet([[0,0,0],[20,0,0],[20,20,5]],5)
    with pytest.raises(ValueError,match="RIBBON_ORIENTATION"):
        C.ribbon_envelope(route,width_mm=8,thickness_mm=.2,width_axis=[0,0,1])

