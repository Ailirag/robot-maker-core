"""Recheck current combined toolpaths and every bound geometry review before export."""
from __future__ import annotations
from collections import defaultdict
from functools import lru_cache
import copy
import hashlib
import io
import json
from pathlib import Path
import zipfile
from engineering import multiplate as M, print_risks as R
from engineering.delivery import read_bound


def fingerprint():
    return hashlib.sha256(b"".join(Path(p).read_bytes() for p in
        (__file__,R.__file__,M.__file__,Path(R.__file__).with_name("print_sections.py")))).hexdigest()


def scan(root,plan):
    raw=read_bound(root,plan["combined"]["project"])
    for job in plan["jobs"]:
        read_bound(root,job["step"])
        read_bound(root,job["project"])
    return copy.deepcopy(_scan(str(Path(root).resolve()),json.dumps(plan["jobs"],sort_keys=True),
                               raw,fingerprint()))


@lru_cache(maxsize=2)
def _scan(root,jobs_json,raw,analyzer):
    from engineering.print_sections import match_parts
    jobs=json.loads(jobs_json);view=M.inspect(io.BytesIO(raw));reports={}
    if len(jobs)!=len(view["plates"]):raise ValueError("PRINT_REVIEW: plate coverage differs")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        cfg=M.xml(z.read("Metadata/model_settings.config"))
        names={o.get("id"):M.metadata(o)["name"] for o in cfg.findall("object")}
        for job,plate,node in zip(jobs,view["plates"],cfg.findall("plate")):
            labels={M.metadata(i)["identify_id"]:names[M.metadata(i)["object_id"]]
                    for i in node.findall("model_instance")}
            data=z.read(f'Metadata/plate_{plate["index"]}.gcode')
            text=data.decode("utf-8")
            default=next(iter(labels)) if len(labels)==1 and "; start printing object" not in text else None
            if set(plate["names"])!=set(job["parts"]):
                raise ValueError("PRINT_REVIEW: part coverage differs")
            geometry={mesh["name"]:R.geometry_risks(mesh) for mesh in plate["parts"]}
            settings=R.inspect_project(Path(root)/job["project"]["path"])["settings"]
            necks=match_parts(Path(root)/job["step"]["path"],plate["parts"],
                              min_wall_mm=2*float(settings["line_width"]))
            evidence=R.toolpath_evidence(R.iter_toolpaths(text,default_object=default),labels,
                {name:r["regions"] for name,r in geometry.items()},
                top_gap_mm=float(settings["support_top_z_distance"]),layer_height_mm=float(settings["layer_height"]),
                initial_layer_height_mm=float(settings["initial_layer_print_height"]))
            parts=[]
            for mesh in plate["parts"]:
                part=geometry[mesh["name"]];local=evidence[mesh["name"]]
                part["toolpath"]=local["toolpath"];part["annular_necks"]=necks[mesh["name"]]
                if part["annular_necks"]:part["risks"].append("thin_annular_wall")
                for role in R.RISK_ROLES:
                    if role in part["toolpath"]["roles"]:part["risks"].append("slicer_"+role.lower().replace(" ","_"))
                for region in part["regions"]:
                    region["support"]=local["regions"][region["id"]]
                part["status"]="REVIEW_REQUIRED" if part["risks"] else "NO_SCREENING_FINDINGS"
                parts.append(part)
            reports[job["id"]]=dict(schema=R.SCHEMA,project_sha256=M.sha(raw),
                step_sha256=job["step"]["sha256"],analyzer_sha256=analyzer,plate_index=plate["index"],
                gcodes={f'Metadata/plate_{plate["index"]}.gcode':M.sha(data)},parts=parts,physical_approval=False)
    return dict(schema="assembly-print-scan-v1",project_sha256=M.sha(raw),
                analyzer_sha256=analyzer,plates=reports,physical_approval=False)


def verify(root,plan):
    if "print_review" not in plan:raise ValueError("PRINT_REVIEW: bound review required")
    review=json.loads(read_bound(root,plan["print_review"]))
    if "print_scan" not in plan:raise ValueError("PRINT_REVIEW: bound toolpath scan required")
    reports=json.loads(read_bound(root,plan["print_scan"]))
    if (reports.get("schema")!="assembly-print-scan-v1"
            or reports.get("analyzer_sha256")!=fingerprint()
            or reports.get("project_sha256")!=plan["combined"]["project"]["sha256"]
            or set(reports.get("plates",{}))!={j["id"] for j in plan["jobs"]}):
        raise ValueError("PRINT_REVIEW: scan stale; rerun the combined toolpath scan")
    # A scan is immutable producer evidence, like the geometry stages. Rehash
    # its input bytes without rerunning the expensive path parser at every render.
    with zipfile.ZipFile(io.BytesIO(read_bound(root,plan["combined"]["project"]))) as z:
        for i,job in enumerate(plan["jobs"],1):
            report=reports["plates"][job["id"]];read_bound(root,job["step"])
            if (report.get("step_sha256")!=job["step"]["sha256"] or report.get("plate_index")!=i
                    or sorted(p["name"] for p in report["parts"])!=sorted(job["parts"])
                    or report.get("gcodes")!={f"Metadata/plate_{i}.gcode":M.sha(z.read(f"Metadata/plate_{i}.gcode"))}):
                raise ValueError("PRINT_REVIEW: scan input or coverage differs")
    if (review.get("schema")!="assembly-print-reviews-v1"
            or review.get("project_sha256")!=reports["project_sha256"]
            or set(review.get("plates",{}))!=set(reports["plates"])):
        raise ValueError("PRINT_REVIEW: stale or incomplete")
    for key,report in reports["plates"].items():R.verify_review(report,review["plates"][key])
    from engineering.geometry_feedback import feedback
    geometries={}
    for name,row in plan["components"].items():
        if row["supply"]!="printed":continue
        if "geometry_review" not in row:raise ValueError("GEOMETRY_REVIEW: required for "+name)
        decision=json.loads(read_bound(root,row["geometry_review"]))
        receipt=json.loads(read_bound(root,decision["receipt"]))
        if (decision.get("part")!=name or receipt.get("input",{}).get("step")!=row["revision"]
                or receipt.get("coverage",{}).get("complete") is not True
                or set(receipt.get("methods",{}))!={"sections","walls","connections"}
                or any(m.get("complete") is not True for m in receipt["methods"].values())):
            raise ValueError("GEOMETRY_REVIEW: stale/incomplete for "+name)
        findings=[f for m in receipt["methods"].values() for f in m["report"]["findings"]]
        result=feedback(findings,decision["decisions"])
        if result!=decision["model_feedback"] or result["unresolved"]:
            raise ValueError("GEOMETRY_REVIEW: unresolved for "+name)
        geometries[name]=dict(review=row["geometry_review"],scan=decision["receipt"])
    return dict(status="REVIEWED",physical_approval=False,project_sha256=reports["project_sha256"],
                print_review=plan["print_review"],geometry=geometries)
