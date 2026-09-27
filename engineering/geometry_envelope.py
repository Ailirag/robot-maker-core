"""Verify an explicitly designed exterior envelope against the delivered STEP.

A bounding box cannot expose local protrusions in a curved wall. The model
declares the permitted envelope (including any intentional projections), datum,
and exclusions; this module measures actual minus permitted B-Rep material.
"""
from functools import lru_cache
import json
from pathlib import Path
from engineering.delivery import read_bound
from engineering.print_package import digest,inspect_project,mesh_report
from engineering.joint_review import _solid

def containment(actual,allowed):
    for shape in (actual,allowed):
        if shape is None or not shape.is_valid or len(shape.solids())!=1 or shape.volume<=1e-8:
            raise ValueError("envelope: expected one valid nonempty solid")
    outside=actual-allowed
    regions=[dict(volume_mm3=round(s.volume,6),
        bounds_mm=[list(s.bounding_box().min),list(s.bounding_box().max)])
        for s in outside.solids() if s.volume>1e-8]
    return dict(status="PASS" if outside.volume<=1e-5 else "OUTSIDE_ENVELOPE",
        outside_mm3=round(outside.volume,6),regions=regions,physical_approval=False)

@lru_cache(maxsize=8)
def _envelope(path,sha):
    from build123d import import_step
    return import_step(path)

def _part(root,job,name):
    project=inspect_project(root/job["project"]["path"])
    meshes=[m for m in project["parts"] if m["name"]==name]
    if len(meshes)!=1:raise ValueError("envelope: ambiguous print object")
    bounds=mesh_report(meshes[0])["bounds_mm"]
    return _solid(str(root/job["step"]["path"]),job["step"]["sha256"],tuple(bounds[0]+bounds[1]))

def verify(root,plan,contracts):
    root=Path(root)
    if "envelope_review" not in plan:raise ValueError("envelope: complete exterior review is required")
    data=json.loads(read_bound(root,plan["envelope_review"]))
    if (data.get("schema")!="geometry-envelope-review-v1"
            or data.get("analyzer_sha256")!=digest(Path(__file__).read_bytes())):
        raise ValueError("envelope: unsupported review or stale analyzer")
    expected={}
    for contract in contracts:
        for job in contract["jobs"]:
            for name in job["parts"]:
                if name in expected:raise ValueError("envelope: duplicate part")
                expected[name]=job
    rows=data.get("parts",{})
    if not isinstance(rows,dict) or set(rows)!=set(expected):raise ValueError("envelope: unreviewed or extra part")
    results={}
    for name,row in rows.items():
        job=expected[name]
        for key in ("step","project","sliced_project"):read_bound(root,job[key])
        if (row.get("job")!=job["id"] or row.get("step_sha256")!=job["step"]["sha256"]
                or row.get("project_sha256")!=job["sliced_project"]["sha256"]):
            raise ValueError("envelope: stale part: "+name)
        if not isinstance(row.get("reason"),str) or not row["reason"].strip():
            raise ValueError("envelope: missing functional reason: "+name)
        if row.get("mode")=="not_applicable":
            if not isinstance(row.get("limitation"),str) or not row["limitation"].strip():
                raise ValueError("envelope: exclusion limitation required")
            results[name]=dict(status="NOT_APPLICABLE",reason=row["reason"],limitation=row["limitation"])
        elif row.get("mode")=="contained":
            if not row.get("datum","").strip():raise ValueError("envelope: datum required")
            read_bound(root,row["definition_source"]);read_bound(root,row["allowed"])
            result=containment(_part(root,job,name),_envelope(str(root/row["allowed"]["path"]),row["allowed"]["sha256"]))
            if result["status"]!="PASS":raise ValueError("envelope: outside allowed exterior: "+name+" "+str(result))
            results[name]=dict(**result,datum=row["datum"],allowed=row["allowed"])
        else:raise ValueError("envelope: unsupported method")
    return dict(schema="geometry-envelope-verification-v1",status="REVIEWED",parts=results,physical_approval=False)
