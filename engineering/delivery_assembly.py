"""Check print delivery against every body in the assembly, including inherited parts."""
from __future__ import annotations
import json
from engineering.delivery import read_bound,verify

def verify_inventory(root,plan,contracts):
    if plan.get("schema")!="assembly-print-inventory-v1":
        raise ValueError("ASSEMBLY_DELIVERY: unsupported inventory")
    read_bound(root,plan["assembly"])
    index=json.loads(read_bound(root,plan["index"]))
    if index.get("geometry_sources",{}).get(plan["assembly"]["path"])!=plan["assembly"]["sha256"]:
        raise ValueError("ASSEMBLY_DELIVERY: index does not bind this assembly")
    bodies=[b["name"] for b in index["bodies"]]
    if not bodies or any(not isinstance(n,str) or not n for n in bodies) or len(set(bodies))!=len(bodies):
        raise ValueError("ASSEMBLY_DELIVERY: invalid assembly body inventory")
    components=plan["components"]
    if set(components)!=set(bodies):
        raise ValueError("ASSEMBLY_DELIVERY: unclassified or extra bodies: "+str(sorted(set(bodies)^set(components))))
    required=[]
    for name,row in components.items():
        if row.get("supply") not in ("printed","external","reserve") or (not isinstance(row.get("reason"),str) or not row["reason"].strip()):
            raise ValueError("ASSEMBLY_DELIVERY: unsupported supply or missing reason: "+name)
        if row["supply"]=="printed":required.append(name)
    alternatives=plan.get("alternatives",{})
    if set(alternatives)&set(bodies):
        raise ValueError("ASSEMBLY_DELIVERY: alternative duplicates assembly")
    for name,row in alternatives.items():
        if not isinstance(name,str) or not name or (not isinstance(row.get("reason"),str) or not row["reason"].strip()):
            raise ValueError("ASSEMBLY_DELIVERY: alternative requires reason")
        read_bound(root,row["source"])
        required.append(name)
    delivered=[];job_ids=[]
    for contract in contracts:
        names=[n for j in contract["jobs"] for n in j["parts"]]
        if sorted(names)!=sorted(contract["required_parts"]):
            raise ValueError("ASSEMBLY_DELIVERY: contract part inventory differs")
        delivered.extend(names);job_ids.extend(j["id"] for j in contract["jobs"])
    if len(job_ids)!=len(set(job_ids)) or len(delivered)!=len(set(delivered)):
        raise ValueError("ASSEMBLY_DELIVERY: duplicate delivered job or part instance")
    if sorted(delivered)!=sorted(required):
        raise ValueError("ASSEMBLY_DELIVERY: missing "+str(sorted(set(required)-set(delivered)))+
                         "; extra "+str(sorted(set(delivered)-set(required))))
    return dict(assembly_bodies=len(bodies),required_printed=sorted(required),
                printed_instances=len(required),alternatives=sorted(alternatives),
                assembly_printed_instances=len(required)-len(alternatives))

def verify_bundle(root,plan,*,authorities=None):
    contracts=[json.loads(read_bound(root,pin)) for pin in plan["deliveries"]]
    coverage=verify_inventory(root,plan,contracts)
    printability=verify_printability(root,plan,contracts)
    from engineering.joint_review import verify as verify_joints
    joints=verify_joints(root,plan,contracts)
    from engineering.geometry_envelope import verify as verify_envelopes
    envelopes=verify_envelopes(root,plan,contracts)
    reports=[verify(root,c,authority=(authorities or {}).get(pin["path"]))
             for pin,c in zip(plan["deliveries"],contracts)]
    jobs=[j for r in reports for j in r["jobs"]]
    allowed=sum(j["fabrication_status"]=="ALLOWED" for j in jobs)
    return dict(schema="assembly-delivery-verification-v1",files_status="VERIFIED",
        fabrication_status="ALLOWED" if allowed==len(jobs) else "PARTIAL" if allowed else "HOLD",
        physical_approval=allowed==len(jobs),coverage=coverage,jobs=jobs,printability=printability,joint_review=joints,envelope_review=envelopes)

def verify_printability(root,plan,contracts):
    from pathlib import Path
    from engineering.print_risks import scan_project,verify_review
    if "printability_reviews" not in plan:
        raise ValueError("PRINTABILITY: complete delivery requires bound risk reviews")
    reviews=json.loads(read_bound(root,plan["printability_reviews"]))
    jobs=[j for c in contracts for j in c["jobs"]]
    if set(reviews)!=set(j["id"] for j in jobs):
        raise ValueError("PRINTABILITY: missing or extra job review")
    return {j["id"]:verify_review(scan_project(Path(root)/j["sliced_project"]["path"],step_path=Path(root)/j["step"]["path"]),reviews[j["id"]])
            for j in jobs}

def verify_archive(path,manifest):
    """Check the actual ZIP, not only the source directory or a filenames list."""
    from pathlib import Path
    import zipfile
    from engineering.print_package import digest
    if manifest.get("schema") not in ("assembly-print-archive-v1","assembly-print-archive-v2","assembly-print-archive-v3","assembly-print-archive-v4","assembly-print-archive-v5","assembly-print-archive-v6"):
        raise ValueError("ARCHIVE: unsupported manifest")
    if manifest["schema"] in ("assembly-print-archive-v2","assembly-print-archive-v3","assembly-print-archive-v4","assembly-print-archive-v5","assembly-print-archive-v6"):
        combined=manifest.get("combined_project",{})
        if (combined.get("plates")!=len(manifest["jobs"])
                or combined.get("parts")!=manifest["coverage"]["printed_instances"]
                or not all(combined.get(k) in manifest["files"]
                           for k in ("project","native_receipt","verification","printability"))):
            raise ValueError("ARCHIVE: complete combined project is required")
    if manifest["schema"] in ("assembly-print-archive-v3","assembly-print-archive-v4","assembly-print-archive-v5","assembly-print-archive-v6") and manifest.get("joint_review") not in manifest["files"]:
        raise ValueError("ARCHIVE: joint verification required")
    if manifest["schema"] in ("assembly-print-archive-v4","assembly-print-archive-v5","assembly-print-archive-v6") and manifest.get("envelope_review") not in manifest["files"]:
        raise ValueError("ARCHIVE: envelope verification required")
    if manifest["schema"] in ("assembly-print-archive-v5","assembly-print-archive-v6") and manifest.get("structural_review") not in manifest["files"]:
        raise ValueError("ARCHIVE: structural verification required")
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or set(names)!=set(manifest["files"])|{"CONTENTS.json"}:
            raise ValueError("ARCHIVE: missing, duplicate or extra member")
        if json.loads(z.read("CONTENTS.json"))!=manifest:
            raise ValueError("ARCHIVE: manifest differs")
        for name,pin in manifest["files"].items():
            raw=z.read(name)
            if digest(raw)!=pin["sha256"] or len(raw)!=pin["bytes"]:
                raise ValueError("ARCHIVE: changed member: "+name)
        if manifest["schema"] in ("assembly-print-archive-v5","assembly-print-archive-v6"):
            structural=json.loads(z.read(manifest["structural_review"]))
            expected_schema = "print-structural-verification-v2" if manifest["schema"]=="assembly-print-archive-v6" else "print-structural-verification-v1"
            if (structural.get("schema")!=expected_schema
                    or structural.get("status")!="REVIEWED"
                    or structural.get("physical_approval") is not False
                    or structural.get("strength_n") is not None
                    or set(structural.get("parts",{}))!=set(manifest["coverage"]["required_printed"])
                    or any(p.get("status")!="REVIEWED" or p.get("unresolved")!=[]
                           for p in structural["parts"].values())):
                raise ValueError("ARCHIVE: incomplete structural verification")
            if manifest["schema"]=="assembly-print-archive-v6":
                for part in structural["parts"].values():
                    weak=part.get("weakness",{})
                    if (weak.get("schema")!="geometry-weakness-scan-v1"
                            or weak.get("coverage",{}).get("complete") is not True
                            or weak.get("physical_approval") is not False
                            or weak.get("strength_n") is not None
                            or any(weak.get(k,{}).get("coverage",{}).get("complete") is not True
                                   for k in ("walls","connections"))):
                        raise ValueError("ARCHIVE: incomplete structural verification of walls/connections")
    return dict(files=len(names),sha256=digest(Path(path).read_bytes()))

def export_archive(root,plan,destination,*,documents):
    """Export every verified print job and independently verify the resulting ZIP."""
    from pathlib import Path
    import zipfile
    from engineering.print_package import digest
    root=Path(root).resolve();destination=Path(destination).resolve()
    target=Path(str(destination)+".zip")
    if destination.exists() or target.exists():
        raise ValueError("ARCHIVE: choose new output paths")
    from engineering.structural_review import verify as verify_structure
    structural=verify_structure(root,plan,[json.loads(read_bound(root,p)) for p in plan["deliveries"]])
    checked=verify_bundle(root,plan)
    from engineering.multiplate_job import ensure
    combined=ensure(root,plan)
    from engineering.multiplate_risks import report as combined_risks
    risk_report=combined_risks(root,plan,combined)
    proof=combined["verification"]
    if proof["plates"]!=len(checked["jobs"]) or proof["parts"]!=checked["coverage"]["printed_instances"]:
        raise ValueError("ARCHIVE: combined project coverage differs")
    if checked["joint_review"]["status"]!="REVIEWED":raise ValueError("ARCHIVE: joint review incomplete")
    files={}
    def add(name,raw):
        if (not name or Path(name).is_absolute() or ".." in Path(name).parts
                or "\\" in name or ":" in name or name=="CONTENTS.json" or name in files):
            raise ValueError("ARCHIVE: unsafe or duplicate output name")
        files[name]=raw
    add("JOINT_VERIFICATION.json",(json.dumps(checked["joint_review"],ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
    add("STRUCTURAL_VERIFICATION.json",(json.dumps(structural,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
    add("ENVELOPE_VERIFICATION.json",(json.dumps(checked["envelope_review"],ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
    combined_name=Path(combined["sliced_project"]["path"]).name
    add(combined_name,read_bound(root,combined["sliced_project"]))
    add("COMBINED_NATIVE_RESULT.json",read_bound(root,combined["native_receipt"]))
    add("COMBINED_VERIFICATION.json",read_bound(root,combined["manifest"]))
    add("COMBINED_PRINTABILITY.json",(json.dumps(risk_report,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
    for pin in plan["deliveries"]:
        contract=json.loads(read_bound(root,pin))
        for job in contract["jobs"]:
            for key,sub in (("sliced_project",""),("step","STEP/"),("stl","STL/")):
                p=job[key];add(sub+Path(p["path"]).name,read_bound(root,p))
    if not documents or not any(n.lower().endswith(".html") for n in documents):
        raise ValueError("ARCHIVE: HTML instructions required")
    for name,pin in documents.items():
        raw=read_bound(root,pin)
        if name.lower().endswith(".html") and (b"<html" not in raw.lower() or b"</html>" not in raw.lower()):
            raise ValueError("ARCHIVE: incomplete HTML")
        add(name,raw)
    manifest=dict(schema="assembly-print-archive-v6",structural_review="STRUCTURAL_VERIFICATION.json",envelope_review="ENVELOPE_VERIFICATION.json",joint_review="JOINT_VERIFICATION.json",
        combined_project=dict(project=combined_name,native_receipt="COMBINED_NATIVE_RESULT.json",
            verification="COMBINED_VERIFICATION.json",printability="COMBINED_PRINTABILITY.json",plates=proof["plates"],parts=proof["parts"]),
        coverage=checked["coverage"],jobs=checked["jobs"],printability=checked["printability"],
        files={name:dict(sha256=digest(raw),bytes=len(raw)) for name,raw in files.items()})
    files["CONTENTS.json"]=(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    destination.mkdir(parents=True)
    with zipfile.ZipFile(target,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,raw in files.items():
            p=destination/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,raw)
    result=verify_archive(target,manifest)
    return dict(directory=str(destination),archive=str(target),archive_sha256=result["sha256"],
                files=result["files"],coverage=checked["coverage"])
