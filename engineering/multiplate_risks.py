"""Recheck reviewed geometry against each combined plate's actual toolpaths.

Interpretations are reusable only after the native geometry/settings comparison
and only while the finding set and every local treatment still pass. This does
not extend physical permission.
"""
from collections import defaultdict
import copy
from functools import lru_cache
import io
import json
from pathlib import Path
import zipfile
from engineering import multiplate as M, print_risks as R
from engineering.delivery import read_bound


def recheck_plate(*, base, review, parts, settings, labels, gcode,
                  project_hash, index, analyzer_hash):
    R.verify_review(base, review)
    old = {p["name"]: p for p in base["parts"]}
    if set(old) != {p["name"] for p in parts} or set(labels.values()) != set(old):
        raise ValueError("MULTIPLATE_RISKS: part inventory differs")
    text = gcode.decode("utf-8")
    default = next(iter(labels)) if len(labels) == 1 and "; start printing object" not in text else None
    geometry={mesh["name"]:R.geometry_risks(mesh) for mesh in parts}
    evidence=R.toolpath_evidence(R.iter_toolpaths(text,default_object=default),labels,
        {name:r["regions"] for name,r in geometry.items()},
        top_gap_mm=float(settings["support_top_z_distance"]),layer_height_mm=float(settings["layer_height"]),
        initial_layer_height_mm=float(settings["initial_layer_print_height"]))
    scanned = []
    for mesh in parts:
        part = geometry[mesh["name"]]
        # Same bound STEP and unchanged local meshes were verified by current().
        part["annular_necks"] = copy.deepcopy(old[mesh["name"]].get("annular_necks", []))
        if part["annular_necks"]:
            part["risks"].append("thin_annular_wall")
        local=evidence[mesh["name"]]
        part["toolpath"] = local["toolpath"]
        for role in R.RISK_ROLES:
            if role in part["toolpath"]["roles"]:
                part["risks"].append("slicer_" + role.lower().replace(" ", "_"))
        if set(part["risks"]) != set(old[mesh["name"]]["risks"]):
            raise ValueError("MULTIPLATE_RISKS: changed findings require a new interpretation: " + mesh["name"])
        for region in part["regions"]:
            region["support"] = local["regions"][region["id"]]
        part["status"] = "REVIEW_REQUIRED" if part["risks"] else "NO_SCREENING_FINDINGS"
        scanned.append(part)
    report = dict(base, project_sha256=project_hash, plate_index=index,
        analyzer_sha256=analyzer_hash, parts=scanned,
        gcodes={f"Metadata/plate_{index}.gcode": M.sha(gcode)}, physical_approval=False)
    rebound = dict(copy.deepcopy(review), project_sha256=project_hash,
                   analyzer_sha256=analyzer_hash)
    result = R.verify_review(report, rebound)
    return dict(source_project_sha256=base["project_sha256"],
        source_review_sha256=M.sha(json.dumps(review,sort_keys=True).encode()),
        basis="Unchanged verified geometry/settings; identical finding set; treatments rechecked against actual combined G-code.",
        report=report, review=rebound, verification=result)


@lru_cache(maxsize=2)
def _report(root, raw, jobs_json, reviews_json, bases_json, analyzer_hash):
    jobs=json.loads(jobs_json);reviews=json.loads(reviews_json);bases=json.loads(bases_json)
    project=M.inspect(io.BytesIO(raw))
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        cfg=M.xml(z.read("Metadata/model_settings.config"))
        objects={o.get("id"):M.metadata(o)["name"] for o in cfg.findall("object")}
        rows={}
        for job,plate,node,base in zip(jobs,project["plates"],cfg.findall("plate"),bases):
            labels={M.metadata(inst)["identify_id"]:objects[M.metadata(inst)["object_id"]]
                    for inst in node.findall("model_instance")}
            rows[job["id"]]=recheck_plate(base=base,review=reviews[job["id"]],
                parts=plate["parts"],settings=project["settings"],labels=labels,
                gcode=z.read(f'Metadata/plate_{plate["index"]}.gcode'),
                project_hash=M.sha(raw),index=plate["index"],analyzer_hash=analyzer_hash)
    if len(rows)!=len(jobs) or len(rows)!=len(project["plates"]):
        raise ValueError("MULTIPLATE_RISKS: incomplete plate coverage")
    return dict(schema="combined-printability-v1",project_sha256=M.sha(raw),
        analyzer_sha256=analyzer_hash,status="REVIEWED",physical_approval=False,plates=rows)


def report(root, plan, record):
    # Caller obtained record from current/ensure, which verifies all native
    # meshes and settings. Rebind reads prevent changes after that comparison.
    raw=read_bound(root,record["sliced_project"])
    contracts=[json.loads(read_bound(root,p)) for p in plan["deliveries"]]
    jobs=[j for c in contracts for j in c["jobs"]]
    reviews=read_bound(root,plan["printability_reviews"])
    bases=[R.scan_project(Path(root)/j["sliced_project"]["path"],
                          step_path=Path(root)/j["step"]["path"]) for j in jobs]
    paths=[Path(__file__),Path(R.__file__),Path(R.__file__).with_name("print_sections.py"),Path(M.__file__)]
    fingerprint=M.sha(b"".join(p.read_bytes() for p in paths))
    return copy.deepcopy(_report(str(Path(root).resolve()),raw,json.dumps(jobs,sort_keys=True),
                                 reviews,json.dumps(bases,sort_keys=True),fingerprint))
