import copy,io,zipfile
from pathlib import Path
import pytest
from engineering.print_package import project_bytes,inspect_project,check_bed,compare_projects,mesh_report

def part(name="a",x=20):
    v=[[x+a,20+b,c] for a,b,c in [(0,0,0),(1,0,0),(0,1,0),(0,0,1)]]
    return dict(name=name,vertices=v,triangles=[[0,2,1],[0,1,3],[0,3,2],[1,2,3]])
def settings():
    return dict(printable_area=["0x0","256x0","256x256","0x256"],printable_height="256",
        bed_exclude_area=[],brim_type="outer_only",brim_width="10",
        printer_settings_id="Sample printer",curr_bed_type="Textured PEI Plate",
        filament_type=["ABS"],nozzle_diameter=["0.4"])

def test_plate_cache_is_local_to_geometry_profile_and_placement(tmp_path,monkeypatch):
    from engineering import print_package_cache as K,artifact_cache as C
    monkeypatch.setattr(K,"PROJECT_CACHE",C.Cache(tmp_path))
    first=K.project_bytes([part()],settings(),"plate")
    assert first==project_bytes([part()],settings(),"plate")
    assert K.project_bytes([part()],settings(),"plate")==first
    assert K.PROJECT_CACHE.stats=={"hit":1,"miss":1}
    changed=settings();changed["wall_loops"]="5"
    K.project_bytes([part()],changed,"plate")
    K.project_bytes([part(x=22)],settings(),"plate")
    assert K.PROJECT_CACHE.stats=={"hit":1,"miss":3}

def test_multipart_roundtrip(tmp_path):
    ps=[part(),part("b",40)];raw=project_bytes(ps,settings(),"sample")
    p=tmp_path/"job.3mf";p.write_bytes(raw)
    loaded=inspect_project(p)
    assert [m["name"] for m in loaded["parts"]]==["a","b"]
    assert compare_projects(dict(parts=ps,settings=settings()),loaded)
    assert raw==project_bytes(ps,settings(),"sample")
def test_brim_counts_for_bed():
    with pytest.raises(ValueError,match="brim"): check_bed([part(x=248)],settings())
def test_open_mesh_refused():
    p=part();p["triangles"].pop()
    with pytest.raises(ValueError,match="MESH"): mesh_report(p)
def test_wrong_winding_refused():
    p=part();p["triangles"][0].reverse()
    with pytest.raises(ValueError,match="MESH"): mesh_report(p)
@pytest.mark.parametrize("change",["missing","scale","material","placement"])
def test_changed_delivery_refused(change):
    p=dict(parts=[part(),part("b",40)],settings=settings());q=copy.deepcopy(p)
    if change=="missing": q["parts"].pop()
    if change in ("scale","placement"):
        for v in q["parts"][0]["vertices"]: v[0]*=1.01
    if change=="material": q["settings"]["filament_type"]=["PLA"]
    with pytest.raises(ValueError,match="DELIVERY"): compare_projects(p,q)
def test_duplicate_names_refused():
    with pytest.raises(ValueError,match="duplicate"): project_bytes([part(),part()],settings(),"x")

def test_native_project_format_marker(tmp_path):
    with zipfile.ZipFile(io.BytesIO(project_bytes([part()],settings(),"x"))) as z:
        assert b"BambuStudio-02.08.02.60" in z.read("3D/3dmodel.model")
        assert b"engineering.print_package" in z.read("3D/3dmodel.model")

@pytest.mark.parametrize("key",["enable_support","brim_width","nozzle_temperature","wall_loops"])
def test_process_changes_after_slicing_refused(key):
    p=dict(parts=[part()],settings=settings());q=copy.deepcopy(p)
    q["settings"][key]="changed"
    with pytest.raises(ValueError,match="setting changed"):compare_projects(p,q)
