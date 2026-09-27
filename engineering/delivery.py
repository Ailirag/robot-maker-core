"""Require final files and producer evidence before reporting a deliverable complete."""
from __future__ import annotations
import json
from pathlib import Path
import zipfile
from engineering.print_package import digest,inspect_project,compare_projects,check_bed

def read_bound(root,entry):
    if not isinstance(entry,dict): raise ValueError("DELIVERY: missing binding")
    rel=entry.get("path")
    if not isinstance(rel,str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError("DELIVERY: invalid path")
    p=(Path(root)/rel).resolve()
    if not p.is_relative_to(Path(root).resolve()): raise ValueError("DELIVERY: outside root")
    if not p.is_file(): raise ValueError("DELIVERY: missing file: "+rel)
    raw=p.read_bytes()
    if digest(raw)!=entry.get("sha256"): raise ValueError("DELIVERY: stale file: "+rel)
    return raw

def verify_print_jobs(root,contract,*,authority=None):
    """File readiness and fabrication permission are independent, explicit results."""
    if contract.get("schema")!="print-delivery-v1": raise ValueError("DELIVERY: unsupported schema")
    jobs=contract.get("jobs",[])
    if not jobs or len({j.get("id") for j in jobs})!=len(jobs): raise ValueError("DELIVERY: invalid job list")
    if not contract.get("sources"): raise ValueError("DELIVERY: sources required")
    for entry in contract["sources"]: read_bound(root,entry)
    geometry=json.loads(read_bound(root,contract["geometry_evidence"]))
    if geometry.get("status")!="PASS" or geometry.get("errors")!=[]:
        raise ValueError("DELIVERY: geometry not verified")
    if {j["id"] for j in geometry["jobs"]}!={j["id"] for j in jobs}:
        raise ValueError("DELIVERY: geometry job set differs")
    results=[]
    names=[]
    for job in jobs:
        names.extend(job["parts"])
        for key in ("project","sliced_project","step","stl","slicer_receipt"):
            read_bound(root,job[key])
        geo=next(g for g in geometry["jobs"] if g["id"]==job["id"])
        if geo.get("step_sha256")!=job["step"]["sha256"] or geo.get("solids")!=len(job["parts"]) or geo.get("collision_pairs")!=[]:
            raise ValueError("DELIVERY: geometry receipt not bound to these parts")
        a=inspect_project(Path(root)/job["project"]["path"])
        b=inspect_project(Path(root)/job["sliced_project"]["path"])
        compare_projects(a,b);check_bed(a["parts"],a["settings"])
        if sorted(p["name"] for p in a["parts"])!=sorted(job["parts"]): raise ValueError("DELIVERY: wrong part set")
        if a["settings"]["filament_type"]!=[job["material"]]: raise ValueError("DELIVERY: wrong material")
        if len(b["gcodes"])!=1: raise ValueError("DELIVERY: real sliced G-code required")
        receipt=json.loads(read_bound(root,job["slicer_receipt"]))
        if receipt.get("return_code")!=0 or receipt.get("error_string")!="Success.":
            raise ValueError("DELIVERY: slicer failed")
        plates=receipt.get("sliced_plates",[])
        if len(plates)!=1 or plates[0].get("warning_message"):
            raise ValueError("DELIVERY: unresolved slicing warnings")
        plate=plates[0]
        if sorted(p["name"] for p in plate["objects"])!=sorted(job["parts"]):
            raise ValueError("DELIVERY: sliced receipt part set differs")
        if plate["total_predication"]<=0 or sum(f["total_used_g"] for f in plate["filaments"])<=0:
            raise ValueError("DELIVERY: missing time or material estimate")
        with zipfile.ZipFile(Path(root)/job["sliced_project"]["path"]) as z:
            raw=z.read(b["gcodes"][0])
            if digest(raw)!=job.get("gcode_sha256") or b"; CHANGE_LAYER" not in raw:
                raise ValueError("DELIVERY: missing or mismatched G-code evidence")
        results.append(dict(id=job["id"],material=job["material"],parts=job["parts"],
            seconds=plate["total_predication"],grams=sum(f["total_used_g"] for f in plate["filaments"]),
            status="VERIFIED"))
    if len(names)!=len(set(names)) or sorted(names)!=sorted(contract["required_parts"]):
        raise ValueError("DELIVERY: complete unique part inventory required")
    # A software status, a render or an exit code never grants physical approval.
    if not isinstance(authority,dict): authority={}

    approved=authority.get("release_allowed") is True
    scope=authority.get("allowed_job_ids",[j["id"] for j in results])
    if not isinstance(scope,list) or not all(isinstance(x,str) for x in scope) or not set(scope)<=set(j["id"] for j in results):
        raise ValueError("DELIVERY: authority job scope differs")
    for job in results:
        job["fabrication_status"]="ALLOWED" if approved and job["id"] in scope else "HOLD"
    granted=sum(j["fabrication_status"]=="ALLOWED" for j in results)
    state="ALLOWED" if granted==len(results) else "PARTIAL" if granted else "HOLD"
    return dict(schema="delivery-verification-v1",files_status="VERIFIED",
        fabrication_status=state,physical_approval=granted==len(results),
        authority=authority,jobs=results)

def verify_instruction(root,entry):
    raw=read_bound(root,entry)
    if b"<html" not in raw.lower() or b"</html>" not in raw.lower():
        raise ValueError("DELIVERY: complete instruction required")
    return True


def verify(root,contract,*,authority=None):
    result=verify_print_jobs(root,contract,authority=authority)
    verify_instruction(root,contract.get("instruction"))
    return result


def verify_scope_evidence(root,scope,manifest,*,full=False):
    """Check a proposed fabrication scope's evidence; never grant permission."""
    from engineering.facts import FactManifest,FactStatus
    if scope.get("schema")!="print-scope-v1":
        raise ValueError("SCOPE_EVIDENCE: unsupported schema")
    contract=json.loads(read_bound(root,scope["delivery"]))
    read_bound(root,scope["protocol"])
    if scope.get("integrity_resolution") is not None:
        recovery=json.loads(read_bound(root,scope["integrity_resolution"]))
        retired=(Path(root)/recovery["historical_root"]).resolve()
        if any((Path(root)/entry["path"]).resolve().is_relative_to(retired)
               for entry in contract["sources"]):
            raise ValueError("SCOPE_EVIDENCE: retired source cannot feed a new print scope")
    entries=contract["sources"]+[contract["geometry_evidence"],contract["instruction"]]+scope.get("checkers",[])+scope.get("authorities",[])
    for job in contract["jobs"]:
        entries.extend(job[k] for k in ("project","sliced_project","step","stl","slicer_receipt"))
    for entry in entries:read_bound(root,entry)
    selected=[j for j in contract["jobs"] if j["id"] in scope["job_ids"]]
    if len(selected)!=len(scope["job_ids"]) or len(set(scope["job_ids"]))!=len(selected):
        raise ValueError("SCOPE_EVIDENCE: exact job set required")
    if any(j["material"]!=scope["material"] for j in selected):
        raise ValueError("SCOPE_EVIDENCE: material differs")
    facts=FactManifest(manifest)
    for fid,binding in scope["facts"].items():
        resolution=facts.resolve(fid,expected_unit=binding["expected_unit"],
            expected_scope=binding["expected_scope"],allowed_provenance=binding["allowed_provenance"],
            require_datum=binding["require_datum"])
        raw=json.dumps(manifest["parameters"].get(fid),ensure_ascii=False,sort_keys=True,
                       separators=(",",":"),allow_nan=False).encode("utf-8")
        if resolution.status!=FactStatus.READY or digest(raw)!=binding["sha256"]:
            raise ValueError("SCOPE_EVIDENCE: changed or unusable fact: "+fid)
        if fid in scope["fact_conditions"] and (resolution.value!=scope["fact_conditions"][fid] or type(resolution.value) is not type(scope["fact_conditions"][fid])):
            raise ValueError("SCOPE_EVIDENCE: physical reference failed: "+fid)
    if full:verify(root,contract)
    return {"job_ids":[j["id"] for j in selected],"part_count":sum(len(j["parts"]) for j in selected)}
