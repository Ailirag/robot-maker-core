
import copy
import io
import zipfile
import pytest
from engineering.facts import FactManifest
from engineering.print_settings import project_bytes, inspect_project, compare_projects
from tests.test_print_package import part, settings
from engineering.print_strength import assess, process_digest

def data():
    process=settings() | dict(layer_height="0.2",wall_loops="2",sparse_infill_density="15%",
        top_shell_layers="5",bottom_shell_layers="5")
    vals=dict(length_mm=(60,"mm"),width_mm=(10,"mm"),height_mm=(5,"mm"),
        force_n=(1,"N"),safety_factor=(2,"ratio"),max_deflection_mm=(2,"mm"),
        flexural_allowable_mpa=(12,"MPa"),shear_allowable_mpa=(6,"MPa"),
        modulus_mpa=(1600,"MPa"),wall_increment_mm=(0.4,"mm"),
        process_identity=(process_digest(process),"text"),model_applicable=(True,"bool"))
    facts={"parameters":{k:dict(value=v,unit=u,provenance="BENCH_VALIDATED",
        scope="sample",source="fixture only",datum="beam longitudinal XY, transverse Z")
        for k,(v,u) in vals.items()}}
    case=dict(id="beam",part="sample",model="rectangular_cantilever_z",
        bindings={k:dict(fact_id=k,scope="sample",obtained_by="measure "+k) for k in vals},
        geometry_sha256="a"*64)
    candidates=[dict(wall_loops="2",sparse_infill_density="15%",top_shell_layers="5",bottom_shell_layers="5"),
        dict(wall_loops="4",sparse_infill_density="45%",top_shell_layers="5",bottom_shell_layers="5")]
    return facts,case,candidates,process

def run(d):
    f,c,options,s=d
    return assess(FactManifest(f),c,options,s,geometry_sha256="a"*64)

def test_known_hollow_section_result():
    r=run(data())
    assert r["state"]=="CALCULATION_PASS"
    first=r["candidates"][0]
    # outer 10x5 minus inner 8.4x3; no infill strength credit.
    inertia=(10*5**3-8.4*3**3)/12
    assert first["section"]["inertia_mm4"]==pytest.approx(inertia)
    assert first["stress_mpa"]==pytest.approx(2*1*60*2.5/inertia)
    assert first["deflection_mm"]==pytest.approx(2*1*60**3/(3*1600*inertia))
    assert r["physical_approval"] is False

def test_demand_selects_sufficient_candidate():
    d=data();d[0]["parameters"]["max_deflection_mm"]["value"]=1.04
    r=run(d)
    assert r["candidates"][0]["passes"] is False
    assert r["candidates"][1]["passes"] is True
    assert r["selected_settings"]["wall_loops"]=="4"

def test_no_infill_strength_credit():
    d=data();d[2][1]["wall_loops"]="2"
    r=run(d)
    assert r["candidates"][0]["stress_mpa"]==r["candidates"][1]["stress_mpa"]
    assert r["candidates"][0]["deflection_mm"]==r["candidates"][1]["deflection_mm"]

@pytest.mark.parametrize("field,change",[
    ("flexural_allowable_mpa",{"value":None,"provenance":"UNKNOWN"}),
    ("force_n",{"unit":"kg"}),("modulus_mpa",{"scope":"other"}),
    ("wall_increment_mm",{"provenance":"VENDOR_SPEC"}),
    ("model_applicable",{"value":False}),
    ("process_identity",{"value":"b"*64}),
])
def test_unqualified_input_cannot_produce_settings(field,change):
    d=data();d[0]["parameters"][field].update(change)
    r=run(d)
    assert r["state"]=="INCOMPLETE"
    assert r["selected_settings"] is None
    assert r["missing"]
    assert all(x["obtained_by"] for x in r["missing"])

@pytest.mark.parametrize("value",[float("nan"),float("inf"),0,-1,True])
def test_invalid_load_refused(value):
    d=data();d[0]["parameters"]["force_n"]["value"]=value
    assert run(d)["state"]=="INVALID"

def test_geometry_change_refused():
    f,c,o,s=data()
    r=assess(FactManifest(f),c,o,s,geometry_sha256="b"*64)
    assert r["state"]=="INCOMPLETE"
    assert r["selected_settings"] is None

def test_no_option_is_not_success():
    d=data();d[0]["parameters"]["force_n"]["value"]=1000
    r=run(d)
    assert r["state"]=="NO_SUITABLE_SETTINGS"
    assert r["selected_settings"] is None

def test_short_beam_is_unsupported():
    d=data();d[0]["parameters"]["length_mm"]["value"]=10
    assert run(d)["state"]=="UNSUPPORTED"

def test_unsupported_model_refused():
    d=data();d[1]["model"]="arbitrary_plate"
    assert run(d)["state"]=="UNSUPPORTED"

@pytest.mark.parametrize("key,value",[("wall_loops","0"),("wall_loops","2.5"),
    ("sparse_infill_density","101%"),("wall_loops",True),("unknown","7")])
def test_bad_export_settings_refused(key,value):
    p=part();p["print_settings"]={key:value}
    with pytest.raises(ValueError,match="SETTING"):
        project_bytes([p],settings(),"sample")

def test_per_part_settings_survive_roundtrip(tmp_path):
    a,b=part(),part("b",40)
    a["print_settings"]={"wall_loops":"4","sparse_infill_density":"45%"}
    path=tmp_path/"job.3mf"
    path.write_bytes(project_bytes([a,b],settings(),"sample"))
    actual=inspect_project(path)
    assert actual["parts"][0]["print_settings"]==a["print_settings"]
    assert actual["parts"][1]["print_settings"]=={}
    assert compare_projects(dict(parts=[a,b],settings=settings()),actual)
    actual["parts"][0]["print_settings"]={}
    with pytest.raises(ValueError,match="part setting"):
        compare_projects(dict(parts=[a,b],settings=settings()),actual)

from engineering.print_strength import prepare, mesh_digest

def preparation():
    d=data()
    ps=[part("sample")]
    d[1]["geometry_sha256"]=mesh_digest(ps[0])
    req={"sample":{"case":d[1],"candidates":d[2]}}
    return d,ps,req

def test_calculation_drives_project_settings(tmp_path):
    d,ps,req=preparation()
    raw,report=prepare(ps,d[3],"sample",req,FactManifest(d[0]))
    assert report["state"]=="CALCULATION_PASS"
    assert raw
    path=tmp_path/"reinforced.3mf";path.write_bytes(raw)
    loaded=inspect_project(path)
    assert loaded["parts"][0]["print_settings"]==report["parts"]["sample"]["selected_settings"]
    assert "print_settings" not in ps[0]

def test_missing_calibration_never_generates_project():
    d,ps,req=preparation()
    d[0]["parameters"]["modulus_mpa"]["value"]=None
    raw,report=prepare(ps,d[3],"sample",req,FactManifest(d[0]))
    assert raw is None
    assert report["state"]=="INCOMPLETE"

def test_each_part_needs_an_explicit_decision():
    d,ps,req=preparation()
    ps.append(part("unreviewed",40))
    raw,report=prepare(ps,d[3],"sample",req,FactManifest(d[0]))
    assert raw is None
    assert "unreviewed" in report["parts"]

def test_explicit_exemption_remains_visible(tmp_path):
    d,ps,req=preparation();ps.append(part("decorative",40))
    req["decorative"]={"exempt_reason":"No structural role in this synthetic example."}
    raw,report=prepare(ps,d[3],"sample",req,FactManifest(d[0]))
    assert raw and report["parts"]["decorative"]["state"]=="EXEMPT"

def test_obsolete_part_name_refuses_generation():
    d,ps,req=preparation()
    req["old"]={"exempt_reason":"old"}
    raw,report=prepare(ps,d[3],"sample",req,FactManifest(d[0]))
    assert raw is None and report["state"]=="INVALID"

def test_all_exempt_is_not_a_passed_strength_calculation():
    d=data();ps=[part("reference")]
    raw,r=prepare(ps,d[3],"sample",{"reference":{"exempt_reason":"Reference fixture only"}},FactManifest(d[0]))
    assert raw and r["state"]=="EXEMPT"

@pytest.mark.parametrize("key",["top_shell_layers","bottom_shell_layers","sparse_infill_pattern"])
def test_global_shell_process_change_after_slicing_refused(key):
    p=dict(parts=[part()],settings=settings());q=copy.deepcopy(p)
    q["settings"][key]="changed"
    with pytest.raises(ValueError,match="setting changed"):
        compare_projects(p,q)

def test_unhandled_load_case_field_is_not_silently_ignored():
    d=data();d[1]["impact_force_n"]=10000
    assert run(d)["state"]=="INVALID"

def test_simply_supported_load_has_expected_deflection():
    d=data();d[1]["model"]="rectangular_simply_supported_z"
    r=run(d)
    s=r["candidates"][0]
    inertia=(10*5**3-8.4*3**3)/12
    assert s["stress_mpa"]==pytest.approx(2*60/4*2.5/inertia)
    assert s["deflection_mm"]==pytest.approx(2*60**3/(48*1600*inertia))

def test_unhandled_per_object_process_setting_is_refused(tmp_path):
    import xml.etree.ElementTree as ET
    config=settings()|{"outer_wall_line_width":"0.42"}
    raw=project_bytes([part()],config,"sample")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        members={name:z.read(name) for name in z.namelist()}
    tree=ET.fromstring(members["Metadata/model_settings.config"])
    ET.SubElement(tree.find("object"),"metadata",key="outer_wall_line_width",value="0.2")
    members["Metadata/model_settings.config"]=ET.tostring(tree)
    path=tmp_path/"changed.3mf"
    with zipfile.ZipFile(path,"w") as z:
        for name,payload in members.items(): z.writestr(name,payload)
    with pytest.raises(ValueError,match="SETTING"):
        inspect_project(path)

def test_malformed_safety_factor_is_reported():
    d=data();d[0]["parameters"]["safety_factor"]["value"]="bad"
    assert run(d)["state"]=="INVALID"
