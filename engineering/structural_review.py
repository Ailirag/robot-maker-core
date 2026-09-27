"""Recompute structural findings for every delivered part before a new export."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from engineering.delivery import read_bound
from engineering.geometry_envelope import _part
from engineering.structural_scan import scan_sections, scan_contact, contact_region, identity
from engineering import weakness_scan
from engineering.geometry_feedback import feedback, identity as feedback_identity, WORKFLOW


def analyzer_identity():
    return dict(**identity(), weakness=weakness_scan.identity(), feedback=feedback_identity(),
                review_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def evaluate(shape, contacts, decisions, *, sampling=None):
    """Only individually reasoned, bounded engineering decisions resolve findings."""
    sections = scan_sections(shape)
    sampling = {} if sampling is None else sampling
    if not isinstance(sampling,dict) or set(sampling)-{"surface_pitch_mm","voxel_pitch_mm"}:
        raise ValueError("structure: unsupported weakness sampling policy")
    weakness = weakness_scan.scan(shape,**sampling)
    if weakness["coverage"]["complete"] is not True:
        raise ValueError("structure: incomplete weakness coverage; refine sampling")
    findings = list(sections["findings"])+weakness["findings"]
    contact_reports = {}
    for name, item in contacts.items():
        result = scan_contact(item["parent"], item["attachment"], axis=item["axis"])
        common = contact_region(item["parent"], item["attachment"])
        missing = max(0., (common-shape).volume) if common.volume > 1e-8 else 0.
        if missing > 1e-5:
            raise ValueError("structure: declared contact material absent from final part: "+name)
        contact_reports[name] = result
        if result["status"] == "REVIEW_REQUIRED":
            # Bind the finding to the declared root, including symmetric roots.
            findings.append(dict(result, id=name+":"+result["id"]))
    model_feedback = feedback(findings, decisions)
    unresolved = model_feedback["unresolved"]
    return dict(sections=sections, weakness=weakness, contacts=contact_reports, findings=findings,
        decisions=decisions, unresolved=unresolved, model_feedback=model_feedback,
        status="REVIEW_REQUIRED" if unresolved else "REVIEWED",
        physical_approval=False, strength_n=None,
        obtained_by=sections["obtained_by"])


def inspect_review(root, plan, contracts):
    """Read bound inputs and rerun geometry; no stored PASS is trusted."""
    from build123d import import_step
    root = Path(root)
    if "structural_review" not in plan:
        raise ValueError("structure: automatic structural review required for new export")
    data = json.loads(read_bound(root,plan["structural_review"]))
    if (data.get("schema") != "print-structural-review-v1"
            or data.get("identity") != analyzer_identity()):
        raise ValueError("structure: unsupported review or stale analyzer/runtime")
    if data.get("strength_n") is not None or not _text(data.get("obtained_by")):
        raise ValueError("structure: review cannot grant strength")
    # This existing inventory is also independently validated by verify_bundle.
    joint_data = json.loads(read_bound(root,plan["joint_review"]))
    expected = {}
    for contract in contracts:
        for job in contract["jobs"]:
            for name in job["parts"]:
                if name in expected:
                    raise ValueError("structure: duplicate part")
                expected[name] = job
    rows = data.get("parts",{})
    if not expected or set(rows) != set(expected) or set(joint_data.get("parts",{})) != set(expected):
        raise ValueError("structure: missing or extra part inventory")
    results = {}
    for name,row in rows.items():
        job = expected[name]
        for key in ("step","project","sliced_project"):
            read_bound(root,job[key])
        if (row.get("job") != job["id"] or row.get("step_sha256") != job["step"]["sha256"]
                or row.get("project_sha256") != job["sliced_project"]["sha256"]
                or row.get("prepared_sha256") != job["project"]["sha256"]):
            raise ValueError("structure: stale geometry/project: "+name)
        if not all(_text(row.get(k)) for k in ("reviewer","inventory_reason")):
            raise ValueError("structure: mechanical inventory review required")
        declared = joint_data["parts"][name]["joints"]
        ids = [r["id"] for r in declared]
        roots = row.get("roots",{})
        if len(set(ids)) != len(ids) or set(roots) != set(ids):
            raise ValueError("structure: missing or extra root inventory")
        contacts = {}
        for key,item in roots.items():
            if not _text(item.get("reason")):
                raise ValueError("structure: root reason required")
            if item.get("mode") == "contact":
                if not _text(item.get("datum")):
                    raise ValueError("structure: common part datum required")
                for field in ("source","parent","attachment"):
                    read_bound(root,item[field])
                contacts[key] = dict(parent=import_step(root/item["parent"]["path"]),
                    attachment=import_step(root/item["attachment"]["path"]),axis=item["axis"])
            elif item.get("mode") == "manual":
                if not _text(item.get("limitation")):
                    raise ValueError("structure: manual root limitation required")
            else:
                raise ValueError("structure: unsupported root mode")
        results[name] = dict(evaluate(_part(root,job,name),contacts,row.get("decisions",{}),
                                     sampling=row.get("sampling")),
            job=job["id"], step=job["step"], project=job["project"], sliced_project=job["sliced_project"],
            roots=roots, reviewer=row["reviewer"], inventory_reason=row["inventory_reason"])
    unresolved = [name for name,report in results.items() if report["status"] != "REVIEWED"]
    return dict(schema="print-structural-verification-v2",identity=analyzer_identity(),
        status="REVIEW_REQUIRED" if unresolved else "REVIEWED",
        parts=results, unreviewed_parts=unresolved, physical_approval=False,
        strength_n=None,obtained_by=data["obtained_by"],
        review=plan["structural_review"],joint_inventory=plan["joint_review"])


def verify(root, plan, contracts):
    report = inspect_review(root,plan,contracts)
    if report["status"] != "REVIEWED":
        raise ValueError("structure: unresolved findings: "+", ".join(report["unreviewed_parts"])+
            "; run structural_review --check with the inventory and a new output; "+
            "read model_feedback, repair confirmed defects in geometry and rescan")
    return report


def draft(root, plan, contracts):
    """Collect exact part bindings and section findings; never invent a review."""
    root=Path(root)
    joint_data=json.loads(read_bound(root,plan["joint_review"]))
    parts={}
    for contract in contracts:
        for job in contract["jobs"]:
            for key in ("step","project","sliced_project"):
                read_bound(root,job[key])
            for name in job["parts"]:
                if name in parts:
                    raise ValueError("structure: duplicate part")
                inventory=joint_data["parts"][name]
                parts[name]=dict(job=job["id"],step_sha256=job["step"]["sha256"],
                    project_sha256=job["sliced_project"]["sha256"],
                    prepared_sha256=job["project"]["sha256"],
                    reviewer="",inventory_reason="",
                    roots={r["id"]:dict(mode="manual",reason="",limitation="")
                           for r in inventory["joints"]},
                    decisions={},section_preview=scan_sections(_part(root,job,name)),
                    weakness_preview=weakness_scan.scan(_part(root,job,name)))
                row=parts[name]
                row["model_feedback"]=feedback(row["section_preview"]["findings"]+
                                                row["weakness_preview"]["findings"])
    if not parts or set(parts)!=set(joint_data["parts"]):
        raise ValueError("structure: missing or extra part inventory")
    return dict(schema="print-structural-review-v1",identity=analyzer_identity(),
        strength_n=None,obtained_by="Qualified load/material evidence or physical testing required.",
        parts=parts)


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    parser.add_argument("--inventory",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--check",action="store_true",
                        help="Recompute the bound review instead of creating an unapproved draft")
    args=parser.parse_args(argv)
    plan=json.loads(args.inventory.read_text(encoding="utf-8"))
    contracts=[json.loads(read_bound(args.root,p)) for p in plan["deliveries"]]
    report=inspect_review(args.root,plan,contracts) if args.check else draft(args.root,plan,contracts)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8",newline="\n") as handle:
        json.dump(report,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write("\n")
    status=report.get("status","DRAFT_REQUIRES_REVIEW")
    print(status+": "+str(args.output))
    print(json.dumps(dict(model_workflow=list(WORKFLOW), full_report=str(args.output),
        parts={name:dict(status=row.get("status","DRAFT_REQUIRES_REVIEW"),
                        unresolved=len(row["model_feedback"]["unresolved"]))
               for name,row in report["parts"].items()}),ensure_ascii=False,indent=2))
    return 0 if status=="REVIEWED" else 2


if __name__=="__main__":
    raise SystemExit(main())
