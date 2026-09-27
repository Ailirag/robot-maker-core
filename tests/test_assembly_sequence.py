"""Independent manipulator fixture: missing parts and uncertain geometry fail closed."""
import copy
import pytest
from engineering import assembly_sequence as S

def model():
    bodies={n:dict(representation="exact",source="fixture CAD") for n in ("deck","carrier","servo","lidar")}
    return dict(schema="assembly-sequence-v1",bodies=bodies,base=["deck"],operations=[
        dict(step="prepare",mode="fixture",fixture="arm",moving=["carrier"]),
        dict(step="servo",mode="insert",fixture="arm",moving=["servo"],direction=[1,0,0]),
        dict(step="arm",mode="transfer",fixture="main",transfer="arm",moving=[],direction=[0,0,-1]),
        dict(step="lidar",mode="insert",fixture="main",moving=["lidar"],direction=[0,0,1])])

def measured(op, shapes):
    return dict(tested_fixed=op["all_fixed"],result=dict(direction=op["direction"],travel_mm=2.,
        step_mm=.5,tolerance_mm3=.001,offsets_checked=5,interferences_by_offset_mm={},
        first_blocked_offset_mm=None,seated_interference_mm3={},blocking_bodies=[],assemblable=True))

def run(m=None,measure=measured):
    m=m or model()
    return S.run(m,{n:object() for n in m["bodies"]},measure=measure)

def test_subassembly_transfer_uses_all_members_and_all_existing_obstacles():
    report=run();rows=report["results"]
    assert rows[0]["moving"]==["servo"] and rows[0]["all_fixed"]==["carrier"]
    assert set(rows[1]["moving"])=={"servo","carrier"} and rows[1]["all_fixed"]==["deck"]
    assert set(rows[2]["all_fixed"])=={"deck","servo","carrier"}
    assert report["status"]=="RIGID_PATHS_CLEAR" and report["physical_approval"] is False
    assert S.validate_report(model(),report)==report

@pytest.mark.parametrize("fault",["missing","duplicate","untransferred","early_transfer","bad_mode","zero_direction","nan","reference"])
def test_plan_rejects_coverage_lifecycle_and_direction_errors(fault):
    m=model()
    if fault=="missing":m["operations"].pop()
    elif fault=="duplicate":m["operations"][-1]["moving"]=["servo"]
    elif fault=="untransferred":m["operations"].pop(2)
    elif fault=="early_transfer":m["operations"][1:3]=reversed(m["operations"][1:3])
    elif fault=="bad_mode":m["operations"][1]["mode"]="skip"
    elif fault=="zero_direction":m["operations"][1]["direction"]=[0,0,0]
    elif fault=="nan":m["operations"][1]["direction"]=[float("nan"),0,1]
    else:m["bodies"]["servo"]["representation"]="reference"
    with pytest.raises(ValueError):run(m)

@pytest.mark.parametrize("kind",["envelope","flexible","unknown"])
@pytest.mark.parametrize("collision",[False,True])
def test_coarse_or_flexible_models_never_prove_assembly_or_exact_defect(kind,collision):
    m=model();m["bodies"]["servo"].update(representation=kind,obtained_by="Measure actual contact surfaces and installation")
    def check(op,shapes):
        row=measured(op,shapes)
        if collision and op["step"]=="servo":
            row["result"].update(interferences_by_offset_mm={"0":{"carrier":1.5}},
                first_blocked_offset_mm=0.,seated_interference_mm3={"carrier":1.5},
                blocking_bodies=["carrier"],assemblable=False)
        return row
    r=run(m,check)
    assert r["status"]=="ACTION_REQUIRED"
    assert r["results"][0]["assessment"]["status"]=="UNRESOLVED"
    assert r["results"][0]["assessment"]["value"] is None
    assert r["results"][0]["assessment"]["obtained_by"]
    assert bool(r["blocked"])==collision

def test_exact_collision_is_preserved_as_defect():
    def check(op,shapes):
        row=measured(op,shapes)
        row["result"].update(interferences_by_offset_mm={"1":{"carrier":2.}},
            first_blocked_offset_mm=1.,blocking_bodies=["carrier"],assemblable=False)
        return row
    m=model()
    # Collision applies only to the servo fixture.
    r=run(m,lambda op,shapes:check(op,shapes) if op["step"]=="servo" else measured(op,shapes))
    assert r["results"][0]["assessment"]["status"]=="DEFECT"

@pytest.mark.parametrize("fault",["missing_result","hidden_hit","unknown_obstacle","stale","fake_clear","nan_volume"])
def test_report_cannot_lose_paths_or_reinterpret_evidence(fault):
    m=model();r=run(m)
    if fault=="missing_result":r["results"].pop()
    elif fault in ("hidden_hit","unknown_obstacle","nan_volume"):
        r["results"][0]["result"]["interferences_by_offset_mm"]={"0":{"foreign" if fault=="unknown_obstacle" else "carrier":float("nan") if fault=="nan_volume" else 2.}}
    elif fault=="stale":m["bodies"]["lidar"]["source"]="different CAD"
    else:r["results"][0]["assessment"]["status"]="APPROVED"
    with pytest.raises(ValueError):S.validate_report(m,r)

def test_measurement_cannot_omit_declared_shape():
    with pytest.raises(ValueError,match="BODY_COVERAGE"):
        S.run(model(),{"deck":object()})

def test_unknown_representation_requires_next_action():
    m=model();m["bodies"]["servo"]["representation"]="envelope"
    with pytest.raises(ValueError):run(m)
