from engineering import cable_routing as C
def v(n):return {"value":n,"obtained_by":"fixture"}
def fixed():
    return dict(id="fixed",fabrication="fixed_length",points=v([[0,0,0],[20,0,0]]),
        diameter_mm=v(1),bend_radius_mm=v(2),clearance_mm=v(0),termination_mm=v([0,0]),
        service_mm=v(5),available_mm=v(30),endpoints_confirmed=True,obstacles_complete=True)
def test_preterminated_cable_has_required_length_not_cut_instruction():
    r=C.assess(fixed(),[])
    assert r["status"]=="CALCULATED"
    assert r["required_length_mm"]==25
    assert r["cut_length_mm"] is None
def test_fixed_stock_needs_useful_length():
    r=fixed();r["available_mm"]=v(None)
    assert C.assess(r,[])["status"]=="INCOMPLETE"
def test_ribbon_cannot_be_validated_as_a_round_wire_without_section_and_frame():
    r=fixed();r["section"]="ribbon"
    result=C.assess(r,[])
    assert result["status"]=="INCOMPLETE"
    assert "RIBBON_SECTION_AND_FRAME_REQUIRED" in result["issues"]

def test_two_ribbons_are_checked_using_their_width_not_an_unknown_round_diameter():
    a=fixed();a.update(section="ribbon",diameter_mm=v(None),ribbon={
        "width_mm":v(8),"thickness_mm":v(.2),"width_axis":v([0,0,1]),"end_width_axis":v([0,0,1])})
    b=dict(a,id="other",points=v([[10,-10,0],[10,10,0]]))
    report=C.inventory([a,b],["fixed","other"],[])
    assert "CABLE_INTERFERENCE:fixed:other" in report["issues"]
    assert all(r["required_length_mm"] is None for r in report["cables"])

def test_known_different_end_frames_are_not_silently_accepted():
    a=fixed();a.update(section="ribbon",diameter_mm=v(None),ribbon={
        "width_mm":v(8),"thickness_mm":v(.2),"width_axis":v([0,0,1]),"end_width_axis":v([0,1,0])})
    assert "RIBBON_ORIENTATION_OR_TWIST" in C.assess(a,[])["issues"]
