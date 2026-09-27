import copy
import pytest
from tests.load_fixture import make_fixture
from engineering.load_runner import apply_slice_facts, mass_stable
from engineering.facts import FactManifest

def test_slice_mass_and_com_transformed_back_into_assembly(tmp_path):
    spec,facts,parts,settings=make_fixture(tmp_path)
    r=dict(source_sha256="a"*64,objects={"beam":dict(mass_g=2.,com_bed_mm=[42,33,3])})
    new=apply_slice_facts(facts,spec,r)
    assert new["parameters"]["beam_mass"]["value"]==.002
    assert new["parameters"]["beam_com"]["value"]==pytest.approx([12,3,3])
    assert new["parameters"]["beam_mass"]["provenance"]=="CALCULATED"
    assert facts["parameters"]["beam_mass"]["value"]==.001

def test_mass_feedback_requires_same_inventory_and_centroid():
    a=dict(objects={"a":dict(mass_g=1.,com_bed_mm=[0,0,0])})
    assert mass_stable(a,copy.deepcopy(a))
    b=copy.deepcopy(a);b["objects"]["a"]["mass_g"]=1.1
    assert not mass_stable(a,b)
    b=copy.deepcopy(a);b["objects"]["a"]["com_bed_mm"]=[1,0,0]
    assert not mass_stable(a,b)
    assert not mass_stable(a,dict(objects={}))

@pytest.mark.parametrize("change",["warning","failure","missing_plate"])
def test_native_receipt_rejects_unresolved_slicing(tmp_path,change):
    import json
    from engineering.load_runner import validate_native_receipt
    r=dict(return_code=0,error_string="Success.",sliced_plates=[dict(warning_message="")])
    if change=="warning": r["sliced_plates"][0]["warning_message"]="unsupported bridge"
    if change=="failure": r["return_code"]=3
    if change=="missing_plate": r["sliced_plates"]=[]
    p=tmp_path/"result.json";p.write_text(json.dumps(r),encoding="utf-8")
    with pytest.raises(ValueError): validate_native_receipt(p)
