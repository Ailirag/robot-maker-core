import hashlib
import json
import pytest
from build123d import Box, Pos, export_step
from engineering import structural_review as R


def thin():
    return (Pos(0,0,-4)*Box(12,12,4)+Box(1.4,2.4,5)+Pos(0,0,4)*Box(12,12,4))


def decision():
    return dict(disposition="accepted_for_geometry_screen",reviewer="fixture engineer",
        reason="fixture only: unloaded decorative stem",limitation="No load capacity established",
        classification="intentional_feature",remedy_considered="Thickening changes decorative function",
        verification_basis="Fixture geometry and unloaded purpose reviewed")


def test_findings_cannot_be_cleared_by_a_stored_pass_or_blanket_decision():
    result = R.evaluate(thin(),{}, {})
    assert result["status"] == "REVIEW_REQUIRED"
    with pytest.raises(ValueError,match="extra finding"):
        R.evaluate(thin(),{},{"all":decision()})
    decisions = {key:decision() for key in result["unresolved"]}
    assert R.evaluate(thin(),{},decisions)["status"] == "REVIEWED"
    key = next(iter(decisions))
    decisions[key].pop("limitation")
    with pytest.raises(ValueError,match="limitation"):
        R.evaluate(thin(),{},decisions)


def test_contact_material_must_exist_in_actual_final_body():
    with pytest.raises(ValueError,match="absent from final"):
        R.evaluate(Pos(20,0,0)*Box(10,10,10),
                   {"root":dict(parent=Box(6,6,6),attachment=Box(6,6,6),axis=0)},{})


def fixture(tmp_path,monkeypatch,shape=None):
    def pin(name,raw):
        p=tmp_path/name
        p.write_bytes(raw)
        return dict(path=name,sha256=hashlib.sha256(raw).hexdigest())
    shape = shape if shape is not None else Box(10,10,10)
    step = tmp_path/"part.step"
    export_step(shape,step)
    job=dict(id="one",parts=["bracket"],
        step=pin("part.step",step.read_bytes()),
        project=pin("prepared.3mf",b"prepared"),
        sliced_project=pin("sliced.3mf",b"sliced"))
    joint=pin("joints.json",json.dumps(dict(parts={"bracket":{"joints":[]}})).encode())
    row=dict(job="one",step_sha256=job["step"]["sha256"],
        project_sha256=job["sliced_project"]["sha256"],prepared_sha256=job["project"]["sha256"],
        reviewer="fixture",inventory_reason="Standalone fixture block without separate roots",roots={},decisions={})
    data=dict(schema="print-structural-review-v1",identity=R.analyzer_identity(),strength_n=None,
        obtained_by="Not a physical strength assessment",parts={"bracket":row})
    plan=dict(joint_review=joint,structural_review=pin("structure.json",json.dumps(data).encode()))
    monkeypatch.setattr(R,"_part",lambda *args:shape)
    def write():
        plan["structural_review"]=pin("structure.json",json.dumps(data).encode())
    return plan,[dict(jobs=[job])],data,write


def test_every_part_is_recomputed_even_with_no_declared_roots(tmp_path,monkeypatch):
    plan,contracts,data,write=fixture(tmp_path,monkeypatch,thin())
    data["status"]="PASS"
    write()
    result=R.inspect_review(tmp_path,plan,contracts)
    assert result["unreviewed_parts"] == ["bracket"]
    with pytest.raises(ValueError,match="unresolved"):
        R.verify(tmp_path,plan,contracts)

def test_weak_walls_are_found_even_when_sections_are_large():
    # All axis section areas exceed the old 6 mm2 policy.
    shape=Box(14,12,.8)
    report=R.evaluate(shape,{}, {})
    assert report["sections"]["findings"]==[]
    assert report["weakness"]["walls"]["findings"]
    assert report["status"]=="REVIEW_REQUIRED"


def test_neck_topology_runs_with_zero_declared_joints():
    shape=Pos(-7,0,0)*Box(8,10,10)+Box(8,2.6,2.6)+Pos(7,0,0)*Box(8,10,10)
    report=R.evaluate(shape,{}, {})
    assert any(f["kind"]=="NARROW_CONNECTION" for f in report["findings"])


def test_incomplete_sampling_cannot_be_waived(monkeypatch):
    from engineering import weakness_scan as W
    original=W.scan
    def incomplete(*args,**kwargs):
        result=original(*args,**kwargs)
        result["coverage"]["complete"]=False
        return result
    monkeypatch.setattr(W,"scan",incomplete)
    with pytest.raises(ValueError,match="coverage"):
        R.evaluate(Box(5,5,5),{}, {})


def test_new_analyzer_binds_wall_and_topology_implementation():
    from engineering import weakness_scan as W
    assert R.analyzer_identity()["weakness"]==W.identity()


@pytest.mark.parametrize("fault",["missing","analyzer","runtime","step","sliced","prepared",
                                  "extra_part","root","strength","reviewer"])
def test_review_binding_and_inventory_fail_closed(tmp_path,monkeypatch,fault):
    plan,contracts,data,write=fixture(tmp_path,monkeypatch)
    if fault=="missing":plan.pop("structural_review")
    elif fault=="analyzer":data["identity"]["analyzer_sha256"]="old"
    elif fault=="runtime":data["identity"]["ocp"]="old"
    elif fault in ("step","sliced","prepared"):
        key={"step":"step","sliced":"sliced_project","prepared":"project"}[fault]
        (tmp_path/contracts[0]["jobs"][0][key]["path"]).write_bytes(b"changed")
    elif fault=="extra_part":data["parts"]["extra"]=data["parts"]["bracket"].copy()
    elif fault=="root":data["parts"]["bracket"]["roots"]={"omitted_in_inventory":{}}
    elif fault=="strength":data["strength_n"]=10
    elif fault=="reviewer":data["parts"]["bracket"]["reviewer"]=""
    if fault!="missing":write()
    with pytest.raises(ValueError):
        R.verify(tmp_path,plan,contracts)


def test_contact_feature_sources_are_hash_bound(tmp_path,monkeypatch):
    plan,contracts,data,write=fixture(tmp_path,monkeypatch)
    joints={"parts":{"bracket":{"joints":[{"id":"foot"}]}}}
    raw=json.dumps(joints).encode()
    (tmp_path/"joints.json").write_bytes(raw)
    plan["joint_review"]["sha256"]=hashlib.sha256(raw).hexdigest()
    root=dict(mode="contact",reason="Foot joins frame",datum="final print part",
        axis=0,source=dict(path="definition.py",sha256="stale"),
        parent=contracts[0]["jobs"][0]["step"],attachment=contracts[0]["jobs"][0]["step"])
    (tmp_path/"definition.py").write_text("foot",encoding="utf-8")
    data["parts"]["bracket"]["roots"]={"foot":root}
    write()
    with pytest.raises(ValueError,match="stale file"):
        R.verify(tmp_path,plan,contracts)


def test_full_block_review_passes_without_invented_load_capacity(tmp_path,monkeypatch):
    plan,contracts,data,write=fixture(tmp_path,monkeypatch)
    result=R.verify(tmp_path,plan,contracts)
    assert result["status"]=="REVIEWED"
    assert result["strength_n"] is None and result["physical_approval"] is False


def test_new_archive_refuses_missing_structural_review_before_export(tmp_path,monkeypatch):
    from engineering import delivery_assembly as D
    monkeypatch.setattr(D,"verify_bundle",lambda *a,**k:pytest.fail("expensive export started before gate"))
    with pytest.raises(ValueError,match="structural review required"):
        D.export_archive(tmp_path,dict(deliveries=[]),tmp_path/"export",documents={})
    assert not (tmp_path/"export").exists() and not (tmp_path/"export.zip").exists()


def test_draft_collects_findings_and_bindings_without_approving_them(tmp_path,monkeypatch):
    plan,contracts,data,write=fixture(tmp_path,monkeypatch,thin())
    draft=R.draft(tmp_path,plan,contracts)
    assert draft["identity"]==R.analyzer_identity()
    row=draft["parts"]["bracket"]
    assert row["step_sha256"]==contracts[0]["jobs"][0]["step"]["sha256"]
    assert row["section_preview"]["findings"]
    assert row["decisions"]=={} and row["reviewer"]==""
    assert draft["strength_n"] is None


def test_real_step_and_print_mesh_mapping_is_used(tmp_path,monkeypatch):
    from engineering.geometry_envelope import _part as real_part
    from engineering.print_package import project_bytes, stl_mesh
    from build123d import export_stl
    shape=Pos(20,20,6)*thin()
    plan,contracts,data,write=fixture(tmp_path,monkeypatch,shape)
    export_stl(shape,tmp_path/"fixture.stl")
    mesh=dict(name="bracket",**stl_mesh((tmp_path/"fixture.stl").read_bytes()))
    settings=dict(filament_type=["ABS"],brim_type="no_brim",printable_height="256",
                  printable_area=["0x0","256x0","256x256","0x256"])
    raw=project_bytes([mesh],settings,"fixture")
    job=contracts[0]["jobs"][0]
    for key,row_key in (("project","prepared_sha256"),("sliced_project","project_sha256")):
        (tmp_path/job[key]["path"]).write_bytes(raw)
        job[key]["sha256"]=hashlib.sha256(raw).hexdigest()
        data["parts"]["bracket"][row_key]=job[key]["sha256"]
    write()
    monkeypatch.setattr(R,"_part",real_part)
    with pytest.raises(ValueError,match="unresolved"):
        R.verify(tmp_path,plan,contracts)
