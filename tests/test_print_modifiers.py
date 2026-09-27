import copy,io,zipfile
import pytest
from engineering.print_modifiers import project_bytes,inspect_project,compare_projects
from tests.test_print_package import part,settings

def sample():
    p=part();m=copy.deepcopy(p);m["name"]="reinforce"
    m["vertices"]=[[v[0],v[1],v[2]*.5] for v in m["vertices"]]
    m["print_settings"]={"wall_loops":"4","sparse_infill_density":"45%"}
    p["modifiers"]=[m]
    return [p]

def test_modifier_is_not_a_second_printed_part(tmp_path):
    p=tmp_path/"p.3mf";raw=project_bytes(sample(),settings(),"fixture");p.write_bytes(raw)
    r=inspect_project(p)
    assert len(r["parts"])==1 and len(r["parts"][0]["modifiers"])==1
    assert r["parts"][0]["modifiers"][0]["print_settings"]["wall_loops"]=="4"
    assert compare_projects(r,r)
    assert raw==project_bytes(sample(),settings(),"fixture")

def test_lost_or_changed_modifier_is_refused(tmp_path):
    p=tmp_path/"p.3mf";p.write_bytes(project_bytes(sample(),settings(),"fixture"))
    before=inspect_project(p)
    for key in ("modifiers","vertices","print_settings"):
        after=copy.deepcopy(before)
        if key=="modifiers": after["parts"][0]["modifiers"]=[]
        elif key=="vertices": after["parts"][0]["modifiers"][0]["vertices"][0][0]+=.2
        else: after["parts"][0]["modifiers"][0]["print_settings"]["wall_loops"]="2"
        with pytest.raises(ValueError): compare_projects(before,after)

def test_unknown_volume_role_refused(tmp_path):
    raw=project_bytes(sample(),settings(),"fixture")
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(output,"w") as dst:
        for name in src.namelist():
            b=src.read(name)
            if name=="Metadata/model_settings.config": b=b.replace(b"modifier_part",b"negative_part")
            dst.writestr(name,b)
    p=tmp_path/"p.3mf";p.write_bytes(output.getvalue())
    with pytest.raises(ValueError): inspect_project(p)
