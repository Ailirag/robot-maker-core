import copy,json,hashlib
import pytest
from engineering.delivery_assembly import verify_inventory

def fixture(root):
    def put(name,data):
        raw=data if isinstance(data,bytes) else json.dumps(data).encode()
        (root/name).write_bytes(raw)
        return dict(path=name,sha256=hashlib.sha256(raw).hexdigest())
    step=put("assembly.step",b"frozen geometry")
    index=put("index.json",dict(bodies=[dict(name=n) for n in ("base","inherited_mount","motor")],
        geometry_sources={"assembly.step":step["sha256"]}))
    plan=dict(schema="assembly-print-inventory-v1",assembly=step,index=index,
        components={"base":dict(supply="printed",reason="current design"),
                    "inherited_mount":dict(supply="printed",reason="reused printed interface"),
                    "motor":dict(supply="external",reason="purchased motor")},
        alternatives={"spare":dict(reason="alternative sensor",source=put("spare.step",b"spare geometry"))})
    jobs=[dict(id="A",parts=["base","spare"]),dict(id="F",parts=["inherited_mount"])]
    contracts=[dict(jobs=[j],required_parts=j["parts"]) for j in jobs]
    return plan,contracts

def test_complete_assembly_includes_reused_parts(tmp_path):
    p,c=fixture(tmp_path)
    r=verify_inventory(tmp_path,p,c)
    assert r["required_printed"]==["base","inherited_mount","spare"]
    assert r["assembly_bodies"]==3

def test_locally_complete_body_package_is_incomplete_assembly(tmp_path):
    p,c=fixture(tmp_path)
    with pytest.raises(ValueError,match="inherited_mount"):verify_inventory(tmp_path,p,c[:1])

@pytest.mark.parametrize("fault",["unclassified","duplicate","extra","unknown_supply","empty_reason","null_reason","required_mismatch","missing_source","stale_assembly","changed_index_source"])
def test_inventory_refuses_omission_or_unbound_exception(tmp_path,fault):
    p,c=fixture(tmp_path)
    if fault=="unclassified":p["components"].pop("inherited_mount")
    if fault=="duplicate":c[1]["jobs"][0]["parts"].append("base")
    if fault=="extra":c[1]["jobs"][0]["parts"].append("unknown")
    if fault=="unknown_supply":p["components"]["inherited_mount"]["supply"]="ignore"
    if fault=="empty_reason":p["components"]["motor"]["reason"]=""
    if fault=="null_reason":p["components"]["motor"]["reason"]=None
    if fault=="required_mismatch":c[0]["required_parts"]=[]
    if fault=="missing_source":p["alternatives"]["spare"]["source"]["path"]="missing.step"
    if fault=="stale_assembly":(tmp_path/"assembly.step").write_bytes(b"changed")
    if fault=="changed_index_source":
        data=json.loads((tmp_path/"index.json").read_bytes());data["geometry_sources"]={}
        raw=json.dumps(data).encode();(tmp_path/"index.json").write_bytes(raw)
        p["index"]["sha256"]=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):verify_inventory(tmp_path,p,c)
