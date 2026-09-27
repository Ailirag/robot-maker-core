import copy
import pytest
from engineering.print_risks import geometry_risks, toolpaths, support_evidence, verify_review

def roof():
    # A closed prism with a 4 mm high cantilever; its lowest vertex is on the bed.
    polygon=[(0,0),(2,0),(2,4),(8,4),(8,6),(0,6)]
    vertices=[[x,y,z] for y in (0,2) for x,z in polygon]
    # Ends triangulated explicitly, sides oriented outward.
    cap=[[0,1,2],[0,2,3],[0,3,4],[0,4,5]]
    triangles=cap+[[c+6,b+6,a+6] for a,b,c in cap]
    for a in range(6):
        b=(a+1)%6;triangles.extend([[a,a+6,b+6],[a,b+6,b]])
    return dict(name="bracket",vertices=vertices,triangles=triangles)

def test_on_bed_solid_still_reports_elevated_underside():
    r=geometry_risks(roof())
    assert r["bed_contact_mm2"]==pytest.approx(4)
    assert r["regions"][0]["bounds_mm"]==[[2,0,4],[8,2,4]]
    assert r["regions"][0]["projected_area_mm2"]==pytest.approx(12)
    assert r["status"]=="REVIEW_REQUIRED"

def test_open_mesh_never_gets_a_printability_verdict():
    p=roof();p["triangles"].pop()
    with pytest.raises(ValueError,match="MESH"):geometry_risks(p)

def gcode(x=3,z=3.8,role="Support interface"):
    return f"""G90
M83
G1 X{x} Y0.5 Z{z}
; FEATURE: {role}
; start printing object, unique label id: 1
G1 X{x+3} E1
G1 E-1
G1 X{x+4} E1
"""

def test_support_flag_or_unrelated_toolpath_does_not_prove_local_support():
    region=geometry_risks(roof())["regions"][0]
    assert support_evidence(region,toolpaths(gcode()),top_gap_mm=.2)["interface_length_mm"]==pytest.approx(3)
    for text in (gcode(x=40),gcode(z=1),gcode(role="Custom"),gcode().replace("E1","E0")):
        assert support_evidence(region,toolpaths(text),top_gap_mm=.2)["interface_length_mm"]==0

def test_retraction_recovery_is_not_deposited_support():
    p=toolpaths(gcode())
    assert sum(s["length_mm"] for s in p)==pytest.approx(3)

def test_unknown_geometry_modes_fail_closed():
    with pytest.raises(ValueError):toolpaths("G20\n"+gcode())
    with pytest.raises(ValueError):toolpaths(gcode().replace("G1 X6", "G2 R9 X6"))

def test_arc_is_sampled_and_bound_to_its_actual_height():
    p=toolpaths("G90\nM83\nG1 X1 Y0 Z1\n; FEATURE: Support interface\nG3 X-1 Y0 I-1 J0 E1")
    assert sum(s["length_mm"] for s in p)==pytest.approx(3.14159,rel=.01)
    assert all(s["a"][2]==1 and s["b"][2]==1 for s in p)

def report():
    return dict(schema="print-risk-report-v1",project_sha256="a"*64,analyzer_sha256="b"*64,
        parts=[dict(name="bracket",risks=["elevated_underside"],regions=[
            dict(id="region-1",support=dict(interface_length_mm=3,bridge_length_mm=0))],
            toolpath=dict(roles={"Support interface":dict(length_mm=3)}))])
def review():
    return dict(schema="print-risk-review-v1",project_sha256="a"*64,analyzer_sha256="b"*64,
        parts={"bracket":dict(reason="Cantilever has an actual interface below it.",
            treatment="supports",regions={"region-1":dict(treatment="supports",
            reason="Toolpaths below the elevated roof were checked.")})})
def test_every_region_needs_a_bound_review_even_if_slicer_has_no_warning():
    assert verify_review(report(),review())["status"]=="REVIEWED"
    for fault in ("missing_part","missing_region","stale","no_support","empty_reason"):
        r=report();v=review()
        if fault=="missing_part":v["parts"]={}
        if fault=="missing_region":v["parts"]["bracket"]["regions"]={}
        if fault=="stale":v["project_sha256"]="c"*64
        if fault=="no_support":r["parts"][0]["regions"][0]["support"]["interface_length_mm"]=0
        if fault=="empty_reason":v["parts"]["bracket"]["reason"]=None
        with pytest.raises(ValueError,match="PRINTABILITY"):verify_review(r,v)

def test_bridge_review_requires_explicit_geometry_reason_and_actual_bridge():
    r=report();v=review()
    v["parts"]["bracket"]["regions"]["region-1"]["treatment"]="bridge"
    with pytest.raises(ValueError,match="PRINTABILITY"):verify_review(r,v)
    r["parts"][0]["toolpath"]["roles"]["Bridge"]={"length_mm":4}
    with pytest.raises(ValueError,match="PRINTABILITY"):verify_review(r,v)
    r["parts"][0]["regions"][0]["support"]["bridge_length_mm"]=4
    assert verify_review(r,v)["physical_approval"] is False

def test_single_object_gcode_can_use_verified_external_identity():
    text=gcode().replace("; start printing object, unique label id: 1\n","")
    assert {s["object"] for s in toolpaths(text,default_object="9")}=={"9"}

def test_layered_overhang_requires_local_toolpaths():
    r=report();v=review();d=v["parts"]["bracket"]["regions"]["region-1"]
    d["treatment"]="overhang"
    with pytest.raises(ValueError,match="PRINTABILITY"):verify_review(r,v)
    r["parts"][0]["regions"][0]["support"]["overhang_length_mm"]=2
    assert verify_review(r,v)["status"]=="REVIEWED"

def test_small_bed_contact_must_have_actual_bed_adhesion_treatment():
    r=report();v=review();r["parts"][0]["risks"].append("small_bed_contact")
    v["parts"]["bracket"]["bed_contact"]={"reason":"Wide support base starts at the first layer."}
    with pytest.raises(ValueError,match="PRINTABILITY"):verify_review(r,v)
    r["parts"][0]["toolpath"]["roles"]["Support"]={"length_mm":100,"z_min_mm":.2}
    assert verify_review(r,v)["status"]=="REVIEWED"
