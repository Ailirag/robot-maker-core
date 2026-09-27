from pathlib import Path
"""Required material is checked in B-Rep; semantic review cannot clear a gap."""
import pytest
from build123d import Box,Pos,Compound
def test_contiguous_part_with_narrow_root_is_refused():
    from engineering.joint_review import material_coverage
    wall=Pos(0,10,0)*Box(30,3,4)
    rib=Pos(0,0,0)*Box(7,20,4)
    required=Pos(0,7,0)*Box(7,6,4)
    notch=Pos(-2.5,8,0)*Box(2,3,5)
    actual=(wall+rib)-notch
    assert actual.is_valid and len(actual.solids())==1
    result=material_coverage(actual,required)
    assert result["status"]=="MISSING_MATERIAL"
    assert result["missing_mm3"]>1
    assert material_coverage(actual+notch,required)["status"]=="PASS"
@pytest.mark.parametrize("bad",[Box(0.1,0.1,0.1)+Pos(10,0,0)*Box(1,1,1),None])
def test_probe_must_be_one_nonempty_valid_solid(bad):
    from engineering.joint_review import material_coverage
    with pytest.raises(ValueError):material_coverage(Box(5,5,5),bad)
def test_missing_semantic_joint_inventory_is_refused(tmp_path):
    from engineering.joint_review import verify
    with pytest.raises(ValueError,match="joint"):
        verify(tmp_path,{},[])

def review_fixture(tmp_path):
    import json
    from engineering.print_package import digest
    from engineering import joint_review as J
    def pin(name,raw):
        (tmp_path/name).write_bytes(raw)
        return dict(path=name,sha256=digest(raw),bytes=len(raw))
    job=dict(id="plate",parts=["arm"],step=pin("part.step",b"fixture step"),
             sliced_project=pin("part.3mf",b"fixture project"))
    data=dict(schema="print-joint-review-v1",analyzer_sha256=digest(Path(J.__file__).read_bytes()),
              strength_n=None,obtained_by="Qualified bench test.",
              parts={"arm":dict(job="plate",step_sha256=job["step"]["sha256"],
                project_sha256=job["sliced_project"]["sha256"],load_path="Fastener to wall",
                reason="Continuous material path inspected",joints=[dict(id="root",method="engineering_review",
                  reason="A broad tapered web",limitation="No qualified printed strength")])})
    return data,job,pin

@pytest.mark.parametrize("fault",[None,"part","source","project","analyzer","reason","limitation","unknown_method"])
def test_semantic_review_is_complete_and_bound(tmp_path,fault):
    import json
    from engineering import joint_review as J
    data,job,pin=review_fixture(tmp_path)
    if fault=="part":data["parts"]={}
    if fault=="source":(tmp_path/"part.step").write_bytes(b"changed step")
    if fault=="project":data["parts"]["arm"]["project_sha256"]="old"
    if fault=="analyzer":data["analyzer_sha256"]="old"
    if fault=="reason":data["parts"]["arm"]["load_path"]=""
    if fault=="limitation":data["parts"]["arm"]["joints"][0]["limitation"]=""
    if fault=="unknown_method":data["parts"]["arm"]["joints"][0]["method"]="slicer_success"
    plan=dict(joint_review=pin("review.json",json.dumps(data).encode()))
    if fault:
        with pytest.raises(ValueError):J.verify(tmp_path,plan,[dict(jobs=[job])])
    else:assert J.verify(tmp_path,plan,[dict(jobs=[job])])["status"]=="REVIEWED"

def test_complete_bundle_requires_joint_review(tmp_path,monkeypatch):
    import json
    from engineering import delivery_assembly as D
    data,job,pin=review_fixture(tmp_path)
    plan=dict(deliveries=[pin("delivery.json",json.dumps(dict(jobs=[job])).encode())])
    monkeypatch.setattr(D,"verify_inventory",lambda *a:{})
    monkeypatch.setattr(D,"verify_printability",lambda *a:{})
    monkeypatch.setattr(D,"verify",lambda *a,**kw:dict(jobs=[dict(fabrication_status="HOLD")]))
    with pytest.raises(ValueError,match="joint"):D.verify_bundle(tmp_path,plan)
