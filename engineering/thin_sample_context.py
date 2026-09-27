"""Replay every recorded thin sample; add context without accepting a finding.

The caller must bind the original complete report and exact geometry by hashes.
This sidecar reproduces the report's thin subset, not a new whole-surface scan.
It deliberately does not change the wall scanner or historical scan identity.
"""
import hashlib
import json
import math
from pathlib import Path

NORMAL_ANGLE_DEG=15.
COMPARE_MM=2e-6

def identity():
    from engineering import weakness_scan as W
    return dict(context_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                geometric_runtime=W.identity())

def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()

def replay(shape,report,*,expected_identity,max_surface_samples=None):
    from build123d import Face,Vector
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from engineering import weakness_scan as W
    def refuse(message):raise ValueError("THIN_CONTEXT: "+message)
    started=identity()
    if expected_identity!=started["geometric_runtime"]:refuse("scanner/runtime changed")
    if not shape.is_valid or len(shape.solids())!=1:refuse("one valid solid required")
    if (not isinstance(report,dict) or report.get("schema")!="surface-thickness-scan-v1"
            or report.get("coverage",{}).get("complete") is not True):
        refuse("complete wall report required")
    report_hash=_digest(report)
    policy=report.get("policy",{})
    if (policy.get("wall_limit_mm")!=W.WALL_LIMIT_MM or
            policy.get("mesh_tolerance_mm")!=W.MESH_TOLERANCE_MM):
        refuse("scanner policy differs")
    try:
        pitch=W._pitch(policy.get("max_surface_pitch_mm"),W.SURFACE_PITCH_MM)
        budget=W._surface_budget(policy.get("max_samples") if max_surface_samples is None else max_surface_samples)
    except ValueError as exc:refuse(str(exc))
    faces=list(shape.faces())
    if report.get("sampled_faces")!=len(faces):refuse("face inventory changed")
    findings=report.get("findings")
    if not isinstance(findings,list):refuse("findings required")
    ids=set();indices=set()
    for finding in findings:
        if not isinstance(finding,dict):refuse("invalid finding")
        data={k:v for k,v in finding.items() if k!="id"}
        if finding.get("id")!=W._finding(data)["id"]:refuse("finding identity changed")
        index=finding.get("face")
        if (finding.get("kind")!="THIN_WALL" or type(index) is not int
                or index<0 or index>=len(faces) or index in indices or finding["id"] in ids):
            refuse("duplicate or invalid face/finding")
        if finding.get("face_bounds_mm")!=[W._round(v) for v in W._bounds(faces[index])]:
            refuse("face bounds changed")
        if type(finding.get("thin_samples")) is not int or finding["thin_samples"]<=0:
            refuse("positive thin sample count required")
        indices.add(index);ids.add(finding["id"])
    rays=W._Rays(shape.solids()[0]);rows=[];seeds=0
    for finding in findings:
        face=faces[finding["face"]];bounds0=finding["bounds_mm"]
        hits=[];minimum=None;skipped=0;row_seeds=0
        contexts={k:0 for k in ("OPPOSING_SURFACES","OBLIQUE_EXIT","AMBIGUOUS_EXIT")}
        examples={k:[] for k in contexts}
        for seed in W._triangles(face,pitch):
            seeds+=1;row_seeds+=1
            if seeds>budget:refuse("surface sampling budget exceeded")
            # Conservative padding includes projection tolerance and rounded bounds.
            # Replay equality below is mandatory: pruning cannot silently lose a hit.
            if any(v<bounds0[0][k]-W.MESH_TOLERANCE_MM*2-COMPARE_MM or
                   v>bounds0[1][k]+W.MESH_TOLERANCE_MM*2+COMPARE_MM for k,v in enumerate(seed)):
                skipped+=1;continue
            projection=GeomAPI_ProjectPointOnSurf(seed.to_pnt(),face.geom_adaptor())
            if projection.NbPoints()==0 or projection.LowerDistance()>W.MESH_TOLERANCE_MM*2:
                refuse("surface projection failed")
            point=Vector(projection.NearestPoint());direction=-face.normal_at(point)
            values=rays.crossings(tuple(point),tuple(direction),-W.TOL*10,W.WALL_LIMIT_MM+W.TOL)
            positive=[v for v in values if v>W.TOL]
            if not any(abs(v)<W.TOL*10 for v in values):
                if face.distance_to(point)<=W.MESH_TOLERANCE_MM*2:continue
                refuse("unresolved inward chord")
            if not positive:continue
            thickness=positive[0]
            if not rays.inside(point+direction*(thickness/2)):refuse("chord crosses air")
            if thickness>=W.WALL_LIMIT_MM-W.TOL:continue
            minimum=thickness if minimum is None else min(minimum,thickness)
            hits.append(tuple(point))
            exits=[i for i in range(1,rays.engine.NbPnt()+1)
                   if abs(rays.engine.WParameter(i)-thickness)<=W.TOL]
            angles=[]
            for i in exits:
                target=Face(rays.engine.Face(i));exitpoint=Vector(rays.engine.Pnt(i))
                dot=min(1.,abs(target.normal_at(exitpoint).dot(direction)))
                angles.append(math.degrees(math.acos(dot)))
            key=("AMBIGUOUS_EXIT" if len(angles)!=1 else
                 "OPPOSING_SURFACES" if angles[0]<=NORMAL_ANGLE_DEG else "OBLIQUE_EXIT")
            contexts[key]+=1
            if len(examples[key])<4:
                examples[key].append(dict(chord_mm=[list(point),list(point+direction*thickness)],
                    thickness_mm=thickness,exit_angles_deg=angles))
        bounds=([W._round(min(p[k] for p in hits) for k in range(3)),
                 W._round(max(p[k] for p in hits) for k in range(3))] if hits else None)
        if (len(hits)!=finding["thin_samples"] or minimum is None or
                abs(minimum-finding["min_thickness_mm"])>COMPARE_MM or bounds!=bounds0):
            refuse("recorded thin subset changed: "+finding["id"])
        rows.append(dict(id=finding["id"],face=finding["face"],thin_samples=len(hits),
            contexts=contexts,examples=examples,seed_count=row_seeds,
            seeds_outside_recorded_bounds=skipped,original_thin_subset_reproduced=True))
    if identity()!=started or _digest(report)!=report_hash:refuse("inputs changed during replay")
    return dict(schema="thin-sample-context-v1",status="COMPLETE_RECORDED_CONTEXT",
        identity=started,report_sha256=report_hash,findings=rows,
        scope="Every thin sample recorded in the supplied complete wall report",
        coverage=dict(findings=len(rows),thin_samples=sum(r["thin_samples"] for r in rows),
            seed_count=seeds,complete_recorded_subset=True),
        policy=dict(normal_angle_deg=NORMAL_ANGLE_DEG,compare_tolerance_mm=COMPARE_MM,
                    max_surface_samples=budget),
        requires_engineering_review=True,physical_approval=False,strength_n=None,
        obtained_by="Review sections, material paths, load cases and physical joints separately.",
        limits="Context neither accepts nor removes findings. Oblique wedges may be weak. "
               "Exact original report/geometry binding is the caller's obligation; new omitted "
               "findings and unsampled features require the original full scanner.")
