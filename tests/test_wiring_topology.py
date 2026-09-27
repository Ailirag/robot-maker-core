"""A different robot has a servo, lidar and pneumatic valve; no project imports."""
import copy
import pytest
from engineering import wiring_topology as W

def contract(stage="operate"):
    return dict(schema="wiring-topology-v1",stage=stage,terminals={
        "supply.out":"POWER","branch":"POWER","servo.v":"POWER","lidar.v":"POWER",
        "logic.tx":"BUS","lidar.rx":"BUS","valve.enable":"ENABLE","logic.out":"ENABLE"},
        required=[
            dict(id="feed",source="supply.out",target="branch"),
            dict(id="servo",source="branch",target="servo.v"),
            dict(id="lidar",source="branch",target="lidar.v"),
            dict(id="data",source="logic.tx",target="lidar.rx"),
            *([dict(id="valve",source="logic.out",target="valve.enable")] if stage=="operate" else [])],
        unused=[] if stage=="operate" else ["logic.out","valve.enable"],
        connected_groups=[["supply.out","servo.v","lidar.v"]],
        internal_links=[],unresolved=[])

def harness(c):
    return dict(stage=c["stage"],cables=[dict(r,net=c["terminals"][r["source"]]) for r in c["required"]])

@pytest.mark.parametrize("stage",["bench","operate"])
def test_stage_topology_and_branch_connectivity(stage):
    c=contract(stage);r=W.verify(c,harness(c))
    assert r["status"]=="PASS" and r["conductors"]==(5 if stage=="operate" else 4)
    assert r["physical_approval"] is False

@pytest.mark.parametrize("fault",["missing","duplicate","short","wrong_same_net","extra","wrong_stage","bad_unused","disconnect","unknown_port","self","duplicate_requirement"])
def test_omissions_and_wrong_endpoints_are_rejected(fault):
    c=contract();p=harness(c)
    if fault=="missing":p["cables"].pop()
    elif fault=="duplicate":p["cables"].append(copy.deepcopy(p["cables"][0]))
    elif fault=="short":p["cables"][0]["target"]="lidar.rx"
    elif fault=="wrong_same_net":p["cables"][1]["target"]="lidar.v"
    elif fault=="extra":p["cables"].append(dict(id="extra",source="branch",target="servo.v",net="POWER"))
    elif fault=="wrong_stage":p["stage"]="bench"
    elif fault=="bad_unused":c["unused"]=["servo.v"]
    elif fault=="disconnect":
        c["connected_groups"].append(["supply.out","valve.enable"])
    elif fault=="unknown_port":c["required"][0]["source"]="ghost";p["cables"][0]["source"]="ghost"
    elif fault=="self":c["required"][0]["target"]="supply.out";p["cables"][0]["target"]="supply.out"
    else:c["required"].append(copy.deepcopy(c["required"][0]))
    with pytest.raises(ValueError):W.verify(c,p)

def test_unknown_power_port_stays_unresolved_without_invented_conductor():
    c=contract();c["unresolved"]=[dict(id="camera_power",value=None,obtained_by="Confirm board power interface")]
    result=W.verify(c,harness(c))
    assert result["status"]=="ACTION_REQUIRED" and result["unresolved"]==c["unresolved"]

def test_disconnected_net_groups_are_checked_even_without_specific_wire_requirements():
    c=contract();c["required"]=c["required"][1:]
    with pytest.raises(ValueError,match="CONNECTIVITY"):W.verify(c,harness(c))
    c["internal_links"]=[["supply.out","branch"]]
    assert W.verify(c,harness(c))["status"]=="PASS"

@pytest.mark.parametrize("assignments",[
    {"servo":"P1","valve":"P1"}, {"servo":"P2","valve":"P4"}, {"servo":"P1"}, {"servo":"P9","valve":"P4"}])
def test_pin_allocator_refuses_conflicts_missing_or_unavailable_resources(assignments):
    with pytest.raises(ValueError,match="PIN_CONFLICT"):
        W.verify_assignments(["servo","valve"],assignments,["P1","P2","P4"],{"laser":"P2"})

def test_resources_are_scoped_by_controller_by_the_project():
    assert W.verify_assignments(["servo","valve"],{"servo":"control:P1","valve":"aux:P1"},
        ["control:P1","aux:P1"],{})["status"]=="PASS"
