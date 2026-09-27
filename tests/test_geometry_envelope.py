"""Local protrusions must fail even when the overall bounding box is unchanged."""
import json
from pathlib import Path
import pytest
from build123d import Box,Cylinder,Pos,Align

def test_bounding_box_cannot_detect_a_local_boss_outside_curved_wall():
    from engineering.geometry_envelope import containment
    envelope=Cylinder(10,5,align=(Align.CENTER,Align.CENTER,Align.MIN))
    wall=envelope-Cylinder(8,6,align=(Align.CENTER,Align.CENTER,Align.MIN))
    actual=wall+Pos(7.5,7.5,2)*Box(2,2,2)
    assert list(actual.bounding_box().min)==pytest.approx(list(wall.bounding_box().min))
    assert list(actual.bounding_box().max)==pytest.approx(list(wall.bounding_box().max))
    r=containment(actual,envelope)
    assert r["status"]=="OUTSIDE_ENVELOPE" and r["outside_mm3"]>0
    assert r["regions"] and r["physical_approval"] is False
    assert containment(actual & envelope,envelope)["status"]=="PASS"

@pytest.mark.parametrize("shape",[None,Box(.1,.1,.1)+Pos(4,0,0)*Box(1,1,1)])
def test_empty_or_disconnected_part_is_not_a_pass(shape):
    from engineering.geometry_envelope import containment
    with pytest.raises(ValueError):containment(shape,Box(5,5,5))

def test_explicit_envelope_can_include_an_intended_external_feature():
    from engineering.geometry_envelope import containment
    base=Box(10,10,10)
    lug=Pos(6,0,0)*Box(4,3,3)
    actual=base+lug
    assert containment(actual,base)["status"]=="OUTSIDE_ENVELOPE"
    assert containment(actual,base+lug)["status"]=="PASS"

def fixture(root,monkeypatch):
    from engineering import geometry_envelope as E
    from engineering.print_package import digest
    def pin(name,content):
        raw=content if isinstance(content,bytes) else json.dumps(content).encode()
        (root/name).write_bytes(raw)
        return dict(path=name,sha256=digest(raw),bytes=len(raw))
    job=dict(id="plate",parts=["shell"],step=pin("shell.step",b"step"),
        project=pin("prepared.3mf",b"prepared"),sliced_project=pin("sliced.3mf",b"sliced"))
    data=dict(schema="geometry-envelope-review-v1",analyzer_sha256=digest(Path(E.__file__).read_bytes()),
        parts={"shell":dict(job="plate",step_sha256=job["step"]["sha256"],
          project_sha256=job["sliced_project"]["sha256"],mode="contained",
          reason="Curved exterior with no external projections.",datum="print bed XYZ, mm",
          definition_source=pin("definition.py",b"independent design envelope"),
          allowed=pin("envelope.step",b"envelope"))})
    monkeypatch.setattr(E,"_part",lambda *a:Box(10,10,10))
    monkeypatch.setattr(E,"_envelope",lambda *a:Box(12,12,12))
    return E,data,job,pin

@pytest.mark.parametrize("fault",[None,"missing_part","stale_project","stale_analyzer","empty_reason",
    "unknown_mode","missing_datum","stale_envelope","stale_definition","outside"])
def test_review_is_complete_bound_and_enforced(tmp_path,monkeypatch,fault):
    E,data,job,pin=fixture(tmp_path,monkeypatch);row=data["parts"]["shell"]
    if fault=="missing_part":data["parts"]={}
    if fault=="stale_project":row["project_sha256"]="old"
    if fault=="stale_analyzer":data["analyzer_sha256"]="old"
    if fault=="empty_reason":row["reason"]=""
    if fault=="unknown_mode":row["mode"]="looks_good"
    if fault=="missing_datum":row.pop("datum")
    if fault=="stale_envelope":(tmp_path/"envelope.step").write_bytes(b"changed")
    if fault=="stale_definition":(tmp_path/"definition.py").write_bytes(b"changed")
    if fault=="outside":monkeypatch.setattr(E,"_part",lambda *a:Box(20,10,10))
    plan=dict(envelope_review=pin("review.json",data))
    if fault:
        with pytest.raises(ValueError):E.verify(tmp_path,plan,[dict(jobs=[job])])
    else:assert E.verify(tmp_path,plan,[dict(jobs=[job])])["parts"]["shell"]["status"]=="PASS"

def test_complete_delivery_requires_envelope_review(tmp_path,monkeypatch):
    from engineering import delivery_assembly as D,joint_review as J
    raw=json.dumps(dict(jobs=[])).encode()
    from engineering.print_package import digest
    (tmp_path/"delivery.json").write_bytes(raw)
    p=dict(deliveries=[dict(path="delivery.json",sha256=digest(raw))])
    monkeypatch.setattr(D,"verify_inventory",lambda *a:{})
    monkeypatch.setattr(D,"verify_printability",lambda *a:{})
    monkeypatch.setattr(J,"verify",lambda *a:dict(status="REVIEWED"))
    monkeypatch.setattr(D,"verify",lambda *a,**kw:dict(jobs=[]))
    with pytest.raises(ValueError,match="envelope"):D.verify_bundle(tmp_path,p)
