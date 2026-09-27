"""Portable assembly lifecycle, sampled insertion survey and evidence limits.

Projects supply identities, representations and operations. Approximate or
flexible geometry cannot prove installation or turn a coarse overlap into an
exact defect. Physical acceptance and tool/cable access are separate obligations.
"""
from copy import deepcopy
import hashlib
import itertools
import json
import math
from pathlib import Path

def _fail(message):
    raise ValueError(message)

def _text(v):
    return isinstance(v,str) and bool(v.strip())

def _number(v):
    return type(v) in (int,float) and math.isfinite(v)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()

def assessor():
    return digest({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (Path(__file__),Path(__file__).with_name("assembly_fit.py"))})

def _direction(value):
    if value == "radial_inward":
        return value
    if (not isinstance(value,list) or len(value)!=3
            or not all(_number(v) for v in value) or not any(value)):
        _fail("PATH_DIRECTION")
    norm=math.sqrt(sum(v*v for v in value))
    return [v/norm for v in value]

def compile_plan(model):
    """Expand fixture transfers and reconcile every physical body exactly once."""
    if model.get("schema")!="assembly-sequence-v1":
        _fail("PATH_CONTRACT")
    bodies=model.get("bodies")
    if not isinstance(bodies,dict) or not bodies:
        _fail("PATH_BODY_COVERAGE")
    for name,row in bodies.items():
        if not _text(name) or not isinstance(row,dict) or row.get("representation") not in {"exact","envelope","flexible","unknown","reference"}:
            _fail("PATH_REPRESENTATION")
        if not _text(row.get("source")):
            _fail("PATH_REPRESENTATION_SOURCE")
        if row["representation"] in {"envelope","flexible","unknown"} and not _text(row.get("obtained_by")):
            _fail("PATH_REPRESENTATION: obtained_by required")
    physical={n for n,r in bodies.items() if r["representation"]!="reference"}
    base=model.get("base")
    if not isinstance(base,list) or not base or len(base)!=len(set(base)) or not set(base)<=physical:
        _fail("PATH_BASE")
    fixtures={"main":set(base)}; introduced=set(base); keys=set(); compiled=[]
    operations=model.get("operations")
    if not isinstance(operations,list) or not operations:
        _fail("PATH_OPERATIONS")
    for op in operations:
        if not isinstance(op,dict) or not _text(op.get("step")) or op["step"] in keys:
            _fail("PATH_STEP: missing or duplicate")
        keys.add(op["step"])
        mode=op.get("mode");fixture=op.get("fixture");names=op.get("moving")
        if (not _text(fixture) or not isinstance(names,list) or len(names)!=len(set(names))
                or not set(names)<=physical):
            _fail("PATH_BODY_COVERAGE")
        if mode=="fixture":
            if fixture in fixtures or not names or set(names)&introduced:
                _fail("PATH_DUPLICATE_FIXTURE_OR_BODY")
            fixtures[fixture]=set(names);introduced.update(names)
            continue
        if mode not in {"insert","transfer"} or fixture not in fixtures:
            _fail("PATH_FIXTURE_LIFECYCLE")
        direction=_direction(op.get("direction"))
        if mode=="transfer":
            origin=op.get("transfer")
            if origin not in fixtures or origin in {"main",fixture} or names:
                _fail("PATH_TRANSFER")
            names=sorted(fixtures.pop(origin))
        else:
            if set(names)&introduced:
                _fail("PATH_DUPLICATE_BODY")
            introduced.update(names)
        if names:
            compiled.append(dict(step=op["step"],moving=sorted(names),fixture=fixture,
                all_fixed=sorted(fixtures[fixture]),direction=direction))
        fixtures[fixture].update(names)
    if set(fixtures)!={"main"} or fixtures["main"]!=physical or introduced!=physical:
        _fail("PATH_BODY_COVERAGE: uninstalled body or untransferred fixture")
    return compiled

def _assessment(model,row):
    kinds=model["bodies"];moving=row["moving"];hits=row["result"]["blocking_bodies"]
    interacting=set(moving+hits if hits else moving+row["all_fixed"])
    uncertain=sorted(n for n in interacting if kinds[n]["representation"]!="exact")
    if uncertain:
        return dict(status="UNRESOLVED",value=None,obtained_by="; ".join(
            n+": "+kinds[n]["obtained_by"] for n in uncertain),bodies=uncertain)
    if hits:
        return dict(status="DEFECT",value=False,
            obtained_by="Correct the exact modeled interference and repeat the sampled insertion survey.",bodies=hits)
    return dict(status="CLEAR",value=True,
        obtained_by="Sampled rigid CAD path only; confirm tolerances, tools, cables and physical installation separately.",bodies=[])

def _validate_measurement(row):
    result=row.get("result",{});fixed=row["all_fixed"];tested=row.get("tested_fixed")
    if not isinstance(tested,list) or len(tested)!=len(set(tested)) or not set(tested)<=set(fixed):
        _fail("PATH_OBSTACLES")
    for key in ("travel_mm","step_mm","tolerance_mm3"):
        if not _number(result.get(key)) or result[key]<=0:
            _fail("PATH_MEASUREMENT")
    direction=result.get("direction")
    unit=_direction(direction)
    if unit=="radial_inward" or any(abs(a-b)>1e-5 for a,b in zip(unit,direction)):
        _fail("PATH_MEASUREMENT_DIRECTION")
    expected=row["direction"]
    if expected!="radial_inward" and any(abs(a-b)>1e-5 for a,b in zip(expected,direction)):
        _fail("PATH_MEASUREMENT_DIRECTION")
    count=result.get("offsets_checked")
    if type(count) is not int or count<2:
        _fail("PATH_SAMPLE_COVERAGE")
    from engineering.assembly_fit import _offsets
    if count!=len(_offsets(result["travel_mm"],result["step_mm"],())):
        _fail("PATH_SAMPLE_COVERAGE")
    hits=result.get("interferences_by_offset_mm")
    if not isinstance(hits,dict):
        _fail("PATH_INTERFERENCES")
    offsets={}; blockers=set()
    for offset,volumes in hits.items():
        try:distance=float(offset)
        except (ValueError,TypeError):_fail("PATH_OFFSET")
        if not math.isfinite(distance) or not 0<=distance<=result["travel_mm"] or distance in offsets:
            _fail("PATH_OFFSET")
        if not isinstance(volumes,dict) or not volumes or not set(volumes)<=set(tested):
            _fail("PATH_OBSTACLES")
        if any(not _number(v) or v<=0 for v in volumes.values()):
            _fail("PATH_VOLUME")
        offsets[distance]=volumes;blockers.update(volumes)
    if (result.get("assemblable") is not (not hits)
            or result.get("blocking_bodies")!=sorted(blockers)
            or result.get("first_blocked_offset_mm")!=(max(offsets) if offsets else None)
            or result.get("seated_interference_mm3")!=offsets.get(0.,{})):
        _fail("PATH_VERDICT")

def validate_report(model,report):
    compiled=compile_plan(model)
    if (report.get("schema")!="assembly-sequence-report-v1"
            or report.get("physical_approval") is not False
            or report.get("model_sha256")!=digest(model) or report.get("assessor_sha256")!=assessor()):
        _fail("PATH_SOURCE: stale model or assessor")
    rows=report.get("results")
    if not isinstance(rows,list) or len(rows)!=len(compiled):
        _fail("PATH_COVERAGE")
    for expected,row in zip(compiled,rows):
        if not isinstance(row,dict) or any(row.get(k)!=v for k,v in expected.items()):
            _fail("PATH_COVERAGE")
        _validate_measurement(row)
        if row.get("assessment")!=_assessment(model,row):
            _fail("PATH_ASSESSMENT")
    blocked=[r["step"] for r in rows if not r["result"]["assemblable"]]
    unresolved=[r["step"] for r in rows if r["assessment"]["status"]=="UNRESOLVED"]
    physical=sorted(n for n,r in model["bodies"].items() if r["representation"]!="reference")
    if (report.get("blocked")!=blocked or report.get("unresolved")!=unresolved
            or report.get("physical_bodies")!=physical or report.get("installed")!=physical
            or report.get("status")!=("ACTION_REQUIRED" if blocked or unresolved or any(
                r["representation"] not in {"exact","reference"} for r in model["bodies"].values()) else "RIGID_PATHS_CLEAR")):
        _fail("PATH_VERDICT")
    return report

def measure_freecad(op,shapes,step_mm=.5,tolerance_mm3=.001,start_clearance_mm=2.):
    """Conservative broad phase; exact booleans at sampled path positions."""
    import FreeCAD as App
    import Part
    from engineering.assembly_fit import sweep_insertion
    moving=Part.makeCompound([shapes[n] for n in op["moving"]])
    fixed={n:shapes[n] for n in op["all_fixed"]}
    direction=op["direction"]
    if direction=="radial_inward":
        c=moving.BoundBox.Center
        direction=_direction([-c.x,-c.y,0])
    u=[-v for v in direction]
    def corners(bb):
        return itertools.product((bb.XMin,bb.XMax),(bb.YMin,bb.YMax),(bb.ZMin,bb.ZMax))
    lo=min(sum(a*b for a,b in zip(p,u)) for p in corners(moving.BoundBox))
    hi=max((sum(a*b for a,b in zip(p,u)) for s in fixed.values() for p in corners(s.BoundBox)),default=lo)
    travel=max(0.,hi-lo)+start_clearance_mm
    start=moving.copy();start.translate(App.Vector(*[travel*v for v in u]))
    bb=moving.BoundBox;bs=start.BoundBox
    bounds=([min(getattr(bb,k+"Min"),getattr(bs,k+"Min")) for k in ("X","Y","Z")],
            [max(getattr(bb,k+"Max"),getattr(bs,k+"Max")) for k in ("X","Y","Z")])
    obstacles={n:s for n,s in fixed.items() if all(
        min(bounds[1][i],getattr(s.BoundBox,k+"Max"))-max(bounds[0][i],getattr(s.BoundBox,k+"Min"))>1e-7
        for i,k in enumerate(("X","Y","Z")))}
    return dict(tested_fixed=sorted(obstacles),result=sweep_insertion(
        moving,obstacles,direction,travel,step_mm,tolerance_mm3=tolerance_mm3))

def run(model,shapes,*,measure=None,progress=None):
    """Run every compiled operation; injected measurements support independent tests."""
    compiled=compile_plan(model)
    if set(shapes)!=set(model["bodies"]):
        _fail("PATH_BODY_COVERAGE: geometry and declared inventory differ")
    if measure is None:
        for n,s in shapes.items():
            if model["bodies"][n]["representation"]!="reference" and (not s.isValid() or len(s.Solids)!=1):
                _fail("PATH_BREP: "+n)
        measure=measure_freecad
    rows=[]
    for op in compiled:
        row=dict(deepcopy(op),**measure(deepcopy(op),shapes))
        _validate_measurement(row)
        row["assessment"]=_assessment(model,row);rows.append(row)
        if progress:progress(row)
    blocked=[r["step"] for r in rows if not r["result"]["assemblable"]]
    unresolved=[r["step"] for r in rows if r["assessment"]["status"]=="UNRESOLVED"]
    physical=sorted(n for n,r in model["bodies"].items() if r["representation"]!="reference")
    report=dict(schema="assembly-sequence-report-v1",model_sha256=digest(model),assessor_sha256=assessor(),
        physical_approval=False,results=rows,blocked=blocked,unresolved=unresolved,
        physical_bodies=physical,installed=physical,
        status="ACTION_REQUIRED" if blocked or unresolved or any(
            r["representation"] not in {"exact","reference"} for r in model["bodies"].values()) else "RIGID_PATHS_CLEAR")
    return validate_report(model,report)
