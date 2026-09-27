from engineering import cable_routing as C

def v(n):return {"value":n,"obtained_by":"fixture"}
def spec(name,points):
    return dict(id=name,points=v(points),diameter_mm=v(2),bend_radius_mm=v(2),
        clearance_mm=v(0),termination_mm=v([0,0]),service_mm=v(0),available_mm=v(None),
        endpoints_confirmed=True,obstacles_complete=True)

def test_crossing_wires_cannot_both_get_cut_lengths():
    a=spec("a",[[0,0,0],[20,0,0]])
    b=spec("b",[[10,-10,0],[10,10,0]])
    r=C.inventory([a,b],["a","b"],[])
    assert "CABLE_INTERFERENCE:a:b" in r["issues"]
    assert all(x["cut_length_mm"] is None for x in r["cables"])

def test_overpass_and_parallel_separation():
    a=spec("a",[[0,0,0],[20,0,0]])
    b=spec("b",[[10,-10,3],[10,10,3]])
    c=spec("c",[[0,4,0],[20,4,0]])
    assert not C.inventory([a,b,c],["a","b","c"],[])["issues"]

