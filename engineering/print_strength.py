"""Analytical screening of qualified horizontal rectangular beams.

The load model and printed-shell material data must be qualified explicitly.
This is neither FEA nor a physical acceptance verdict. Infill gets no strength
credit; perforations, torsion, buckling, fatigue, creep and impact are outside
this model. The same declared print orientation must be used for calibration
and manufacturing. Unsupported geometry is never inferred to be a beam.
"""
from __future__ import annotations
import hashlib
import json
import math
from engineering.facts import FactStatus
from engineering.print_settings import validate_part_settings

SCHEMA = "print-strength-screen-v1"
SETTING_KEYS = ("wall_loops", "sparse_infill_density", "top_shell_layers", "bottom_shell_layers")
UNITS = dict(length_mm="mm", width_mm="mm", height_mm="mm", force_n="N",
    safety_factor="ratio", max_deflection_mm="mm", flexural_allowable_mpa="MPa",
    shear_allowable_mpa="MPa", modulus_mpa="MPa", wall_increment_mm="mm",
    process_identity="text", model_applicable="bool")
CALIBRATED = {"flexural_allowable_mpa", "shear_allowable_mpa", "modulus_mpa",
              "wall_increment_mm", "process_identity"}
LIMITS = [
    "Static linear-elastic beam screen; no physical approval.",
    "Uniform rectangular shell, beam axis in XY, force in Z; qualified fixed or simple supports.",
    "No infill strength credit. Local buckling, holes, notches, joints, inserts, torsion, impact, fatigue and creep are not evaluated.",
    "Calibration must cover printed shell bonding, orientation, temperatures and the candidate shell thickness range.",
    "Section dimensions and applicability come from identified geometry, not a bounding-box guess.",
    "Deflection is the Euler-Bernoulli bending component; shear deflection is not evaluated.",
]

def process_digest(settings):
    """Bind shell calibration to every process field except varied shell counts/infill."""
    excluded = {*SETTING_KEYS, "print_settings_id"}
    payload = {k:v for k,v in settings.items() if k not in excluded}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False,
        separators=(",", ":")).encode()).hexdigest()

def _section(b, h, side, bottom, top):
    """Area, neutral axis and inertia of outer rectangle minus its empty core."""
    bi, hi = max(0.0, b - 2*side), max(0.0, h - bottom - top)
    ao, ai = b*h, bi*hi
    yc = bottom + hi/2
    area = ao-ai
    neutral = (ao*h/2-ai*yc)/area
    inertia = b*h**3/12 + ao*(h/2-neutral)**2 - bi*hi**3/12 - ai*(yc-neutral)**2
    # VQ/It: examine neutral axis and both void boundaries. Q is monotone
    # on either side of the neutral axis; web thickness is piecewise constant.
    def q_at(y):
        outer = b*(h-y)**2/2
        lo, upper = max(y, bottom), min(h, bottom+hi)
        inner = bi*(upper-lo)*((upper+lo)/2-y) if upper > lo else 0.0
        return outer-inner
    samples = [neutral]
    if bi and hi:
        samples += [bottom, bottom+hi]
    shear_factor = 0.0
    for y in samples:
        web = b-bi if bi and hi and bottom <= y <= bottom+hi else b
        shear_factor = max(shear_factor, q_at(y)/(inertia*web))
    return dict(area_mm2=area, inertia_mm4=inertia, neutral_axis_mm=neutral,
        extreme_fibre_mm=max(neutral,h-neutral), shear_factor_per_mm2=shear_factor,
        core_area_mm2=ai)

def assess(facts, case, candidates, settings, *, geometry_sha256):
    result = dict(schema=SCHEMA, case_id=case.get("id"), part=case.get("part"),
        state="INCOMPLETE", physical_approval=False, selected_settings=None,
        geometry_sha256=geometry_sha256, consumed_facts=[], missing=[], errors=[],
        candidates=[], limitations=LIMITS.copy())
    def missing(field, reason):
        binding = case.get("bindings", {}).get(field, {})
        result["missing"].append(dict(field=field, value=None, reason=reason,
            obtained_by=binding.get("obtained_by") or "Supply qualified evidence for "+field))
    if case.get("model") not in {"rectangular_cantilever_z", "rectangular_simply_supported_z"}:
        result["state"]="UNSUPPORTED"
        missing("model", "A qualified load model is required; arbitrary parts are not beams.")
        return result
    if (not isinstance(geometry_sha256,str) or len(geometry_sha256)!=64
            or any(c not in "0123456789abcdef" for c in geometry_sha256)
            or case.get("geometry_sha256")!=geometry_sha256):
        missing("geometry_sha256", "Geometry is unidentified or changed.")
    if (set(case)!={"id","part","model","bindings","geometry_sha256"}
            or not isinstance(case.get("bindings"),dict)
            or set(case["bindings"])!=set(UNITS)):
        result["state"]="INVALID"
        result["errors"].append("Unknown or missing load-case fields.")
        return result
    values={}
    for key,unit in UNITS.items():
        binding=case.get("bindings",{}).get(key,{})
        if not all(isinstance(binding.get(k),str) and binding[k].strip()
                   for k in ("fact_id","scope","obtained_by")):
            missing(key, "Explicit fact ID, scope and obtained_by are required.")
            continue
        allowed=("BENCH_VALIDATED",) if key in CALIBRATED else (
            "MEASURED","BENCH_VALIDATED","CALCULATED","REQUIREMENT")
        f=facts.resolve(binding["fact_id"],expected_unit=unit,expected_scope=binding["scope"],
            allowed_provenance=allowed,require_datum=True)
        result["consumed_facts"].append(f.as_dict())
        if f.status!=FactStatus.READY or not f.datum.strip() or not isinstance(f.source,str) or not f.source.strip():
            missing(key, f.status.value+": qualified source and datum required")
            continue
        values[key]=f.value
    for key,v in values.items():
        if key in ("process_identity","model_applicable"): continue
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:
            result["errors"].append(key+": finite positive number required")
    if values.get("model_applicable") is not True:
        missing("model_applicable", "Geometry, load path and exclusions are not qualified.")
    try:
        if values.get("process_identity")!=process_digest(settings):
            missing("process_identity", "Printed-shell calibration does not match this process.")
    except (TypeError,ValueError):
        result["errors"].append("Invalid process data.")
    if not result["errors"] and values.get("safety_factor",1)<1:
        result["errors"].append("safety_factor must be at least one.")
    if result["errors"]:
        result["state"]="INVALID"; return result
    if result["missing"]: return result
    length,b,h=(values[k] for k in ("length_mm","width_mm","height_mm"))
    if length/h < 10:
        result["state"]="UNSUPPORTED"
        missing("length_mm", "Slender-beam scope requires length/height >= 10.")
        return result
    try:
        layer=float(settings["layer_height"])
        if not math.isfinite(layer) or layer<=0: raise ValueError("layer_height must be positive")
        if not candidates: raise ValueError("At least one candidate setting set is required")
        for candidate in candidates:
            if set(candidate)!=set(SETTING_KEYS):
                raise ValueError("Candidate must specify exactly "+", ".join(SETTING_KEYS))
            validate_part_settings(candidate)
            loops=int(candidate["wall_loops"])
            fill=float(candidate["sparse_infill_density"].removesuffix("%"))/100
            bottom=int(candidate["bottom_shell_layers"])*layer
            top=int(candidate["top_shell_layers"])*layer
            if bottom<=0 or top<=0: raise ValueError("Closed top and bottom shells are required")
            s=_section(b,h,loops*values["wall_increment_mm"],bottom,top)
            force=values["force_n"]*values["safety_factor"]
            cantilever=case["model"]=="rectangular_cantilever_z"
            moment=force*length/(1 if cantilever else 4)
            shear=force/(1 if cantilever else 2)
            stress=moment*s["extreme_fibre_mm"]/s["inertia_mm4"]
            tau=shear*s["shear_factor_per_mm2"]
            deflection=force*length**3/((3 if cantilever else 48)*values["modulus_mpa"]*s["inertia_mm4"])
            metrics=(stress,tau,deflection)
            if not all(math.isfinite(x) and x>=0 for x in metrics): raise ValueError("Non-finite calculation")
            passes=(stress<=values["flexural_allowable_mpa"] and tau<=values["shear_allowable_mpa"]
                and deflection<=values["max_deflection_mm"])
            result["candidates"].append(dict(settings=dict(candidate),section=s,
                stress_mpa=stress,shear_stress_mpa=tau,deflection_mm=deflection,passes=passes,
                material_volume_proxy_mm3=length*(s["area_mm2"]+fill*s["core_area_mm2"])))
    except (ValueError,TypeError,KeyError,OverflowError,ZeroDivisionError) as exc:
        result["errors"].append(str(exc));result["state"]="INVALID";return result
    viable=[r for r in result["candidates"] if r["passes"]]
    if viable:
        best=min(viable,key=lambda r:(r["material_volume_proxy_mm3"],
            int(r["settings"]["wall_loops"]),float(r["settings"]["sparse_infill_density"][:-1])))
        result["selected_settings"]=best["settings"]
        result["state"]="CALCULATION_PASS"
    else:
        result["state"]="NO_SUITABLE_SETTINGS"
    return result

def mesh_digest(part):
    """Bind a load model to exact triangles in actual print-bed coordinates."""
    payload={k:part[k] for k in ("vertices","triangles")}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False,
        separators=(",",":")).encode()).hexdigest()

def prepare(parts, settings, title, requirements, facts):
    """Return a project only when every part has a completed screen or explicit exemption."""
    from copy import deepcopy
    from engineering.print_settings import project_bytes
    report=dict(schema=SCHEMA,state="INCOMPLETE",physical_approval=False,parts={},errors=[])
    names=[p["name"] for p in parts]
    if (not isinstance(requirements,dict) or len(names)!=len(set(names)) or not names
            or set(requirements)-set(names)):
        report["state"]="INVALID";report["errors"].append("Part set and requirement set disagree.")
        return None,report
    output=deepcopy(parts)
    for p in output:
        name=p["name"];req=requirements.get(name)
        if not isinstance(req,dict):
            report["parts"][name]=dict(state="INCOMPLETE",selected_settings=None,missing=[
                dict(value=None,obtained_by="Define a qualified load case or an explicit structural exemption.")])
            continue
        reason=req.get("exempt_reason")
        if set(req)=={"exempt_reason"} and isinstance(reason,str) and reason.strip():
            report["parts"][name]=dict(state="EXEMPT",reason=reason)
            continue
        case=req.get("case")
        if (set(req)!={"case","candidates"} or not isinstance(case,dict) or case.get("part")!=name
                or not isinstance(req["candidates"],list)):
            report["parts"][name]=dict(state="INVALID",errors=["Malformed or mismatched part requirement."])
            continue
        result=assess(facts,case,req["candidates"],settings,geometry_sha256=mesh_digest(p))
        report["parts"][name]=result
        if result["state"]=="CALCULATION_PASS":
            p["print_settings"]=dict(result["selected_settings"])
    states={r["state"] for r in report["parts"].values()}
    if "INVALID" in states:
        report["state"]="INVALID";return None,report
    if not states<={"EXEMPT","CALCULATION_PASS"}:
        return None,report
    raw=project_bytes(output,settings,title)
    report["state"]="EXEMPT" if states=={"EXEMPT"} else "CALCULATION_PASS"
    report["prepared_3mf_sha256"]=hashlib.sha256(raw).hexdigest()
    return raw,report

def main():
    import argparse
    from pathlib import Path
    from engineering.facts import FactManifest
    from engineering.print_settings import inspect_project
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project",required=True,type=Path)
    parser.add_argument("--requirements",required=True,type=Path)
    parser.add_argument("--facts",required=True,type=Path)
    parser.add_argument("--destination",required=True,type=Path,help="New directory for report and, if complete, unsliced 3MF")
    args=parser.parse_args()
    if args.destination.exists():
        parser.error("Destination already exists; choose a new directory.")
    original=inspect_project(args.project)
    requirements=json.loads(args.requirements.read_text(encoding="utf-8"))
    raw,report=prepare(original["parts"],original["settings"],args.project.stem,
        requirements,FactManifest.from_path(args.facts))
    report["input_project_sha256"]=hashlib.sha256(args.project.read_bytes()).hexdigest()
    report["requirements_sha256"]=hashlib.sha256(args.requirements.read_bytes()).hexdigest()
    args.destination.mkdir(parents=True)
    (args.destination/"STRENGTH_REPORT.json").write_text(
        json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    if raw is not None: (args.destination/"PREPARED.3mf").write_bytes(raw)
    print(json.dumps(dict(state=report["state"],report=str(args.destination/"STRENGTH_REPORT.json"),
        project=str(args.destination/"PREPARED.3mf") if raw else None),ensure_ascii=False))
    return 0 if raw else 2

if __name__=="__main__":
    raise SystemExit(main())
