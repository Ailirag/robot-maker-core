"""Require a sourced joint inventory and check declared material corridors in STEP.

The model identifies functional roots. This module checks completeness, binding
and exact required material; it does not infer forces or prove printed strength.
"""
from __future__ import annotations
from functools import lru_cache
from copy import deepcopy
import json
from pathlib import Path
from engineering.delivery import read_bound
from engineering.print_package import digest,inspect_project,mesh_report

def material_coverage(actual,required):
    for shape in (actual,required):
        if shape is None or not shape.is_valid or len(shape.solids())!=1 or shape.volume<=1e-8:
            raise ValueError("joint: expected one valid nonempty solid")
    # OCCT booleans may update shared tolerances/p-curves, including inputs.
    # Keep cached source bodies and subsequent probes independent.
    missing=max(0.,(deepcopy(required)-deepcopy(actual)).volume)
    return dict(status="PASS" if missing<=1e-5 else "MISSING_MATERIAL",
                required_mm3=round(required.volume,6),missing_mm3=round(missing,6),
                physical_approval=False)

@lru_cache(maxsize=8)
def _solid(path,sha,bounds):
    from build123d import import_step
    solids=import_step(path).solids()
    matches=[s for s in solids if all(abs(a-b)<.081 for a,b in
        zip(list(s.bounding_box().min)+list(s.bounding_box().max),bounds))]
    if len(matches)!=1:raise ValueError("joint: ambiguous STEP body")
    return matches[0]

def verify(root,plan,contracts):
    root=Path(root)
    if "joint_review" not in plan:raise ValueError("joint: bound semantic inventory is required")
    data=json.loads(read_bound(root,plan["joint_review"]))
    if (data.get("schema")!="print-joint-review-v1"
            or data.get("analyzer_sha256")!=digest(Path(__file__).read_bytes())):
        raise ValueError("joint: stale analyzer or unsupported review")
    if data.get("strength_n") is not None or not data.get("obtained_by","").strip():
        raise ValueError("joint: geometric review cannot grant physical strength")
    jobs={j["id"]:j for c in contracts for j in c["jobs"]}
    expected={name:j for j in jobs.values() for name in j["parts"]}
    rows=data.get("parts",{})
    if set(rows)!=set(expected):raise ValueError("joint: missing or extra reviewed part")
    results={};projects={}
    for name,row in rows.items():
        job=expected[name]
        for key in ("step","sliced_project"):read_bound(root,job[key])
        if (row.get("job")!=job["id"] or row.get("step_sha256")!=job["step"]["sha256"]
                or row.get("project_sha256")!=job["sliced_project"]["sha256"]):
            raise ValueError("joint: stale geometry/project: "+name)
        if not row.get("load_path","").strip() or not row.get("reason","").strip():
            raise ValueError("joint: missing mechanical interpretation: "+name)
        joints=row.get("joints")
        if not isinstance(joints,list) or (not joints and not row.get("exempt_reason","").strip()):
            raise ValueError("joint: list roots or explain their absence: "+name)
        ids=[r.get("id") for r in joints]
        if any(not isinstance(i,str) or not i.strip() for i in ids) or len(set(ids))!=len(ids):
            raise ValueError("joint: duplicate/empty root")
        checked=[]
        for joint in joints:
            if not joint.get("reason","").strip():raise ValueError("joint: missing root decision")
            method=joint.get("method")
            if method=="engineering_review":
                if not joint.get("limitation","").strip():raise ValueError("joint: review limitation required")
                checked.append(dict(id=joint["id"],status="ENGINEERING_REVIEW"))
            elif method=="required_material":
                from build123d import import_step
                read_bound(root,joint["probe"])
                if job["id"] not in projects:
                    projects[job["id"]]=inspect_project(root/job["project"]["path"])
                meshes=[p for p in projects[job["id"]]["parts"] if p["name"]==name]
                if len(meshes)!=1:raise ValueError("joint: ambiguous print object")
                bounds=mesh_report(meshes[0])["bounds_mm"]
                actual=_solid(str(root/job["step"]["path"]),job["step"]["sha256"],tuple(bounds[0]+bounds[1]))
                required=import_step(root/joint["probe"]["path"])
                result=material_coverage(actual,required)
                if result["status"]!="PASS":raise ValueError("joint: missing material "+name+"/"+joint["id"]+" "+str(result))
                checked.append(dict(id=joint["id"],**result))
            else:raise ValueError("joint: unsupported method")
        results[name]=checked
    return dict(schema="print-joint-verification-v1",status="REVIEWED",parts=results,
                physical_approval=False,strength_n=None,obtained_by=data["obtained_by"])
