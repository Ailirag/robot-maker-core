import pytest
import numpy as np
from engineering.load_mesh import mesh_step
from engineering.load_pipeline import prepare_analysis, qualification_digest
from engineering.facts import FactManifest

def test_unknown_real_inputs_produce_instruction_packet_without_project(tmp_path):
    spec={"schema":"load-zones-v1","assembly":{"bodies":[],"cases":[]},"parts":[]}
    r=prepare_analysis(spec,FactManifest({"parameters":{}}),[],{},tmp_path)
    assert r["state"] in {"INVALID","INCOMPLETE"} and r["project"] is None
    assert r["physical_approval"] is False and r["next_actions"]

def test_mesh_worker_requires_existing_step_and_explicit_resolution(tmp_path):
    with pytest.raises(ValueError):
        mesh_step(tmp_path/"absent.step",0,tmp_path)

@pytest.mark.parametrize("change",["missing_material","stale_geometry","stale_process","missing_mount","wrong_unit","boolean_limit"])
def test_real_model_inputs_fail_before_meshing(tmp_path,monkeypatch,change):
    from tests.load_fixture import make_fixture
    spec,facts,parts,settings=make_fixture(tmp_path)
    if change=="missing_material": facts["parameters"]["material0"]["value"]=None
    if change=="stale_geometry": spec["parts"][0]["print_mesh_sha256"]="b"*64
    if change=="stale_process": settings["layer_height"]="0.3"
    if change=="missing_mount": facts["parameters"]["mount"]["value"]=None
    if change=="boolean_limit": facts["parameters"]["material0"]["value"]["allowable_mpa"]=True
    if change=="wrong_unit": facts["parameters"]["payload_mass"]["unit"]="g"
    def forbid(*a,**k): raise AssertionError("Invalid input reached mesher")
    monkeypatch.setattr("engineering.load_pipeline.mesh_step",forbid)
    r=prepare_analysis(spec,FactManifest(facts),parts,settings,tmp_path)
    assert r["project"] is None and r["state"] in {"INCOMPLETE","INVALID"}

def test_full_volume_solver_and_local_modifier(tmp_path):
    import os
    runtime=os.environ.get("LOAD_GMSH_PYTHONPATH")
    if not runtime: pytest.skip("Native Gmsh integration is run with the pinned runtime")
    from tests.load_fixture import make_fixture
    from engineering.print_modifiers import inspect_project
    spec,facts,parts,settings=make_fixture(tmp_path)
    r=prepare_analysis(spec,FactManifest(facts),parts,settings,tmp_path,gmsh_pythonpath=runtime)
    assert r["state"]=="CALCULATION_PASS",r["errors"]
    assert r["parts"]["beam"]["zones"]
    assert r["parts"]["beam"]["zones"][0]["bounds_mm"][0][0]==0
    assert r["parts"]["beam"]["zones"][0]["bounds_mm"][1][0]<24
    p=tmp_path/"result.3mf";p.write_bytes(r["project"])
    loaded=inspect_project(p)
    assert len(loaded["parts"])==1 and loaded["parts"][0]["modifiers"]
    assert loaded["parts"][0]["print_settings"]["wall_loops"]=="2"
    assert loaded["parts"][0]["modifiers"][0]["print_settings"]["wall_loops"]=="4"
