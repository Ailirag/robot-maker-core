"""Regressions for layer placement and roundoff near a 45-degree ramp."""
import copy
import math
import pytest
from engineering import print_risks as R
from tests.test_print_risks import roof, gcode, report, review

def ramp(angle):
    mesh=roof()
    rise=6*math.tan(math.radians(angle))
    for v in mesh["vertices"]:
        if v[0]==8 and v[2]==4:v[2]+=rise
        elif v[2]==6:v[2]=13.
    return mesh

@pytest.mark.parametrize("angle",[44.99995,45.,45.00005,46.])
def test_roundoff_at_threshold_does_not_invent_an_unsupported_roof(angle):
    result=R.geometry_risks(ramp(angle))
    assert result["regions"]==[]

@pytest.mark.parametrize("angle",[0.,30.,44.,44.99])
def test_real_shallower_ramp_remains_a_finding(angle):
    result=R.geometry_risks(ramp(angle))
    assert len(result["regions"])==1
    assert result["regions"][0]["projected_area_mm2"]==pytest.approx(12.)

def region(z=.1):
    return dict(id="region-1",bounds_mm=[[2.,0.,z],[8.,2.,z]])

def local(text=None,r=None,height=.2):
    return R.support_evidence(region() if r is None else r,
        R.toolpaths(gcode(z=.2,role="Bottom surface") if text is None else text),
        top_gap_mm=.2,initial_layer_height_mm=height)

def test_model_deposition_can_explain_a_surface_inside_the_first_layer():
    evidence=local()
    assert evidence["first_layer"]["height_mm"]==.2
    assert evidence["first_layer"]["bounds_within_layer"] is True
    assert evidence["first_layer"]["local_model_length_mm"]==3.
    assert evidence["interface_length_mm"]==0

@pytest.mark.parametrize("text",[
    gcode(x=40,z=.2,role="Bottom surface"),
    gcode(z=.2,role="Bottom surface").replace("Y0.5","Y2.1"),
    gcode(z=.4,role="Bottom surface"),
    gcode(z=.2,role="Bottom surface").replace("Z0.2","Z0.1").replace("G1 X6 E1","G1 X6 Z0.3 E1"),
    gcode(z=.2,role="Support interface"),
    gcode(z=.2,role="Brim"),
    gcode(z=.2,role="Bottom surface").replace("E1","E0"),
])
def test_nearby_higher_support_or_zero_deposition_is_not_first_layer_model(text):
    assert local(text)["first_layer"]["local_model_length_mm"]==0

@pytest.mark.parametrize("z",[-.01,.21,4.])
def test_surface_outside_first_layer_cannot_use_its_deposition(z):
    e=local(r=region(z))
    assert e["first_layer"]["bounds_within_layer"] is False
    assert e["first_layer"]["local_model_length_mm"]==0

@pytest.mark.parametrize("height",[True,0,-.2,float("nan"),float("inf"),"0.2"])
def test_initial_layer_height_must_be_resolved_finite_positive_number(height):
    with pytest.raises(ValueError,match="PRINTABILITY"):local(height=height)

def test_no_first_layer_claim_without_an_explicit_height():
    e=R.support_evidence(region(),R.toolpaths(gcode(z=.2,role="Bottom surface")),top_gap_mm=.2)
    assert "first_layer" not in e or e["first_layer"] is None

def first_layer_report_and_review():
    r=report();v=review()
    r["parts"][0]["regions"][0].update(region())
    r["parts"][0]["regions"][0]["support"]=local()
    d=v["parts"]["bracket"]["regions"]["region-1"]
    d.update(treatment="first_layer",reason="Within the resolved first layer; local positive model deposition exists.")
    v["parts"]["bracket"]["treatment"]="mixed"
    return r,v

def test_review_requires_explicit_first_layer_evidence_and_reason():
    r,v=first_layer_report_and_review()
    assert R.verify_review(r,v)["status"]=="REVIEWED"
    assert R.verify_review(r,v)["physical_approval"] is False
    for fault in ("missing","outside","empty","reason","above"):
        bad=copy.deepcopy(r);decision=copy.deepcopy(v)
        evidence=bad["parts"][0]["regions"][0]["support"]
        if fault=="missing":evidence.pop("first_layer")
        if fault=="outside":evidence["first_layer"]["bounds_within_layer"]=False
        if fault=="empty":evidence["first_layer"]["local_model_length_mm"]=0
        if fault=="reason":decision["parts"]["bracket"]["regions"]["region-1"]["reason"]=""
        if fault=="above":bad["parts"][0]["regions"][0]["bounds_mm"][1][2]=.4
        with pytest.raises(ValueError,match="PRINTABILITY"):R.verify_review(bad,decision)

def test_streaming_first_layer_evidence_stays_with_its_object():
    text=gcode(z=.2,role="Bottom surface")+gcode(x=40,z=.2,role="Bottom surface").replace("id: 1","id: 2")
    e=R.toolpath_evidence(R.iter_toolpaths(text),{"1":"present","2":"elsewhere"},
        {"present":[region()],"elsewhere":[region()]},top_gap_mm=.2,initial_layer_height_mm=.2)
    assert e["present"]["regions"]["region-1"]["first_layer"]["local_model_length_mm"]==3
    assert e["elsewhere"]["regions"]["region-1"]["first_layer"]["local_model_length_mm"]==0
