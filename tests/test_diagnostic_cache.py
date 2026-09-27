"""Diagnostic reuse requires the same individual STEP geometry and analysis policy."""
import copy,hashlib,json
from pathlib import Path
import pytest
from build123d import Box,Compound,Pos,export_step
from engineering import diagnostic_cache as C
from engineering.geometry_feedback import METHOD_SCHEMAS

def bind(root,path):
    raw=(root/path).read_bytes()
    return dict(path=path,sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))

def scene(root,name,*,plate_z=0.,other_width=4.):
    plate=Pos(0,0,plate_z)*Box(10,12,3);plate.label="plate"
    other=Pos(30,0,0)*Box(other_width,4,4);other.label="other"
    export_step(Compound(children=[plate,other]),root/name)
    return bind(root,name)

@pytest.fixture
def setup(tmp_path):
    old=scene(tmp_path,"before.step");new=scene(tmp_path,"after.step",other_width=6.)
    entry=dict(part="plate",scope="test",role="study_only",step=old,
        method_options={"connections":{"max_voxels":64000000}},coordinate_system="world")
    record=dict(schema="test-receipt",input=entry,identity={"algorithm":"v1"},
        coverage={"complete":True},status="REVIEW_REQUIRED",physical_approval=False,strength_n=None,
        method_options=entry["method_options"],geometry={"valid":True,"solids":1},
        methods={k:{"complete":True,"report":{"schema":METHOD_SCHEMAS[k],"coverage":{"complete":True},"findings":[{"id":k}]}} for k in ("sections","walls","connections")})
    (tmp_path/"receipt.json").write_text(json.dumps(record))
    return tmp_path,bind(tmp_path,"receipt.json"),dict(entry,step=new),record


def test_session_imports_each_assembly_once_and_still_rejects_changed_part(setup,monkeypatch):
    import build123d
    root,pin,entry,record=setup
    real=build123d.import_step;calls=[]
    def counted(path):
        calls.append(str(path))
        return real(path)
    monkeypatch.setattr(build123d,"import_step",counted)
    session=C.GeometrySession(root)
    C.reuse_part(root,pin,entry,expected_identity={"algorithm":"v1"},session=session)
    record["input"]["part"]="other"
    (root/"other.json").write_text(json.dumps(record))
    other=dict(entry,part="other")
    with pytest.raises(ValueError,match="individual geometry changed"):
        C.reuse_part(root,bind(root,"other.json"),other,expected_identity={"algorithm":"v1"},session=session)
    assert len(calls)==2
    C.reuse_part(root,pin,entry,expected_identity={"algorithm":"v1"},session=session)
    assert len(calls)==2
    (root/entry["step"]["path"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        C.reuse_part(root,pin,entry,expected_identity={"algorithm":"v1"},session=session)

def test_session_cannot_cross_repository_roots(setup,tmp_path):
    root,pin,entry,_=setup
    session=C.GeometrySession(root/"another")
    with pytest.raises(ValueError,match="session root"):
        C.reuse_part(root,pin,entry,expected_identity={"algorithm":"v1"},session=session)

def test_other_part_change_reuses_all_findings_without_accepting_them(setup):
    root,pin,entry,old=setup
    result=C.reuse_part(root,pin,entry,expected_identity={"algorithm":"v1"})
    assert result["status"]=="REVIEW_REQUIRED"
    assert result["methods"]==old["methods"]
    assert result["input"]==entry and result["reuse_evidence"]["source_receipt"]==pin
    assert result["physical_approval"] is False

@pytest.mark.parametrize("case",["translated","policy","identity","coverage","methods","physical","binding","part","coordinate","method_report","method_coverage","status"])
def test_reuse_refuses_changed_or_unproven_inputs(setup,case):
    root,pin,entry,old=setup;identity={"algorithm":"v1"}
    if case=="translated":entry["step"]=scene(root,"moved.step",plate_z=.1)
    elif case=="policy":entry["method_options"]={"connections":{"max_voxels":32000000}}
    elif case=="identity":identity={"algorithm":"v2"}
    elif case=="coverage":old["coverage"]["complete"]=False
    elif case=="methods":del old["methods"]["walls"]
    elif case=="physical":old["physical_approval"]=True
    elif case=="method_report":del old["methods"]["walls"]["report"]
    elif case=="method_coverage":old["methods"]["walls"]["report"]["coverage"]["complete"]=False
    elif case=="status":old["status"]="SCREENED"
    elif case=="part":entry["part"]="other"
    elif case=="coordinate":entry["coordinate_system"]="local"
    elif case=="binding":entry["step"]["sha256"]="0"*64
    if case in {"coverage","methods","physical","method_report","method_coverage","status"}:
        (root/"receipt.json").write_text(json.dumps(old));pin=bind(root,"receipt.json")
    with pytest.raises(ValueError):C.reuse_part(root,pin,entry,expected_identity=identity)
