"""Cable and harness decisions from explicit facts and geometric paths."""
import itertools
import math
from engineering.cable_path import (EPS, number, point, add, sub, mul, dot, norm, unit, fillet, sample, _bounds, segment_box, polyline_collisions, search, segment_distance, self_interference)
SCHEMA="cable-routing-v1"

def _value(spec,key,unknowns):
    entry=spec.get(key)
    if not isinstance(entry,dict) or "value" not in entry or not str(entry.get("obtained_by","")).strip():
        raise ValueError(key+": value + obtained_by required")
    if entry["value"] is None:unknowns[key]=entry
    return entry["value"]

def assess(spec,obstacles):
    unknowns={};issues=[]
    section=spec.get("section","round");fabrication=spec.get("fabrication","cut_stock")
    if section not in ("round","ribbon") or fabrication not in ("cut_stock","fixed_length"):
        raise ValueError("CABLE_KIND")
    keys=("points","diameter_mm","bend_radius_mm","clearance_mm","termination_mm","service_mm","available_mm")
    values={k:_value(spec,k,unknowns) for k in keys}
    ribbon=None
    if section=="ribbon":
        ribbon_spec=spec.get("ribbon",{})
        fields=("width_mm","thickness_mm","width_axis","end_width_axis")
        recipe={k:ribbon_spec.get(k,{"value":None,"obtained_by":"Measure ribbon "+k+" at the installed connectors."}) for k in fields}
        ribbon={k:_value(recipe,k,unknowns) for k in fields}
        if any(v is None for v in ribbon.values()):
            issues.append("RIBBON_SECTION_AND_FRAME_REQUIRED")
            values["diameter_mm"]=None
        else:
            width=number(ribbon["width_mm"],positive=True);thickness=number(ribbon["thickness_mm"],positive=True)
            ribbon["width_axis"]=unit(point(ribbon["width_axis"]))
            ribbon["end_width_axis"]=unit(point(ribbon["end_width_axis"]))
            values["diameter_mm"]=math.hypot(width,thickness)
            unknowns.pop("diameter_mm",None)
    for key in ("diameter_mm","bend_radius_mm","clearance_mm","service_mm","available_mm"):
        if values[key] is not None:
            number(values[key],positive=key in ("diameter_mm","bend_radius_mm"))
    term=values["termination_mm"]
    if term is not None:
        if not isinstance(term,(list,tuple)) or len(term)!=2: raise ValueError("TWO_TERMINATIONS")
        for n in term:number(n)
    if spec.get("endpoints_confirmed") is not True: issues.append("ENDPOINTS_UNCONFIRMED")
    if spec.get("obstacles_complete") is not True: issues.append("OBSTACLES_INCOMPLETE")
    route=None
    if values["points"] is not None and values["bend_radius_mm"] is not None:
        try:route=fillet(values["points"],values["bend_radius_mm"])
        except ValueError as exc: issues.append(str(exc))
    if route is not None and ribbon is not None and all(v is not None for v in ribbon.values()):
        axis=ribbon["width_axis"]
        if norm(sub(axis,ribbon["end_width_axis"]))>1e-7:
            issues.append("RIBBON_ORIENTATION_OR_TWIST")
        for s in route["primitives"]:
            directions=[unit(sub(s["end"],s["start"]))] if s["kind"]=="line" else [s["axis_u"],s["axis_v"]]
            if any(abs(dot(axis,t))>1e-7 for t in directions):
                if "RIBBON_ORIENTATION_OR_TWIST" not in issues:issues.append("RIBBON_ORIENTATION_OR_TWIST")
    collision_checked=False
    if route is not None and values["diameter_mm"] is not None:
        diameter=values["diameter_mm"]
        bend_thickness=ribbon["thickness_mm"] if ribbon and ribbon["thickness_mm"] is not None else diameter
        if any(s["kind"]=="arc" and s["radius_mm"]<=bend_thickness/2 for s in route["primitives"]):
            issues.append("BEND_TOO_TIGHT_FOR_DIAMETER")
        if self_interference(route,diameter):issues.append("SELF_INTERFERENCE")
    if route is not None and values["diameter_mm"] is not None and values["clearance_mm"] is not None:
        # Inflate by the arc/chord error as well as the physical cable radius.
        error=.02;collision_checked=True
        margin=values["diameter_mm"]/2+values["clearance_mm"]+error
        issues.extend("COLLISION:"+name for name in polyline_collisions(sample(route,error),obstacles,margin))
    required_unknowns=set(unknowns)-{"available_mm"}
    if fabrication=="fixed_length" and values["available_mm"] is None:required_unknowns.add("available_mm")
    blocked=any(i not in ("ENDPOINTS_UNCONFIRMED","OBSTACLES_INCOMPLETE","RIBBON_SECTION_AND_FRAME_REQUIRED") for i in issues)
    candidate=None
    if route is not None and term is not None and values["service_mm"] is not None:
        candidate=route["length_mm"]+sum(term)+values["service_mm"]
    if candidate is not None and values["available_mm"] is not None and candidate>values["available_mm"]+EPS:
        issues.append("STOCK_TOO_SHORT");blocked=True
    status="BLOCKED" if blocked else "INCOMPLETE" if required_unknowns or issues else "CALCULATED"
    return dict(schema=SCHEMA,id=spec["id"],status=status,issues=issues,unknowns=unknowns,
        route=route,route_length_mm=route["length_mm"] if route else None,
        planning_length_mm=candidate,required_length_mm=candidate if status=="CALCULATED" else None,
        cut_length_mm=candidate if status=="CALCULATED" and fabrication=="cut_stock" else None,
        section=section,fabrication=fabrication,
        collision_envelope_diameter_mm=values["diameter_mm"],clearance_mm=values["clearance_mm"],
        clearance_checked=collision_checked,
        stock_checked=values["available_mm"] is not None and candidate is not None)

def inventory(specs,required_ids,obstacles):
    if not specs or not required_ids:raise ValueError("EMPTY_HARNESS")
    ids=[r["id"] for r in specs]
    if len(set(ids))!=len(ids) or len(set(required_ids))!=len(required_ids):
        raise ValueError("DUPLICATE_CABLE")
    issues=["MISSING:"+i for i in sorted(set(required_ids)-set(ids))]
    issues+=["UNEXPECTED:"+i for i in sorted(set(ids)-set(required_ids))]
    rows=[assess(s,obstacles) for s in specs]
    for i,j in itertools.combinations(range(len(rows)),2):
        ra,rb=rows[i],rows[j];sa,sb=specs[i],specs[j]
        if not ra["route"] or not rb["route"]:continue
        dims=[r[k] for r in (ra,rb) for k in ("collision_envelope_diameter_mm","clearance_mm")]
        if any(v is None for v in dims):continue
        threshold=(dims[0]+dims[2])/2+max(dims[1],dims[3])+.04
        pa,pb=sample(ra["route"]),sample(rb["route"])
        if any(segment_distance(a,b,c,d)<=threshold for a,b in zip(pa,pa[1:]) for c,d in zip(pb,pb[1:])):
            issue="CABLE_INTERFERENCE:"+ra["id"]+":"+rb["id"]
            issues.append(issue)
            for row in (ra,rb):
                row["issues"].append(issue);row["status"]="BLOCKED";row["cut_length_mm"]=None;row["required_length_mm"]=None
    return dict(schema=SCHEMA,issues=issues,cables=rows,
        status="CALCULATED" if not issues and all(r["status"]=="CALCULATED" for r in rows) else "ACTION_REQUIRED")

