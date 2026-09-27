"""Resolve inertial loads through an explicit acyclic single-parent mount graph."""
from __future__ import annotations
import numpy as np
from engineering.facts import FactStatus
from engineering.load_solver import finite

SCHEMA="assembly-loads-v1"

def fact_value(facts,binding,unit,report,*,calibrated=False):
    if not isinstance(binding,dict) or not all(isinstance(binding.get(k),str) and binding[k].strip()
                                               for k in ("fact_id","scope","obtained_by")):
        raise ValueError("Fact binding requires fact_id, scope and obtained_by")
    f=facts.resolve(binding["fact_id"],expected_unit=unit,expected_scope=binding["scope"],
        allowed_provenance=("BENCH_VALIDATED",) if calibrated else
            ("MEASURED","BENCH_VALIDATED","CALCULATED","REQUIREMENT"),
        require_datum=True)
    report["consumed_facts"].append(f.as_dict())
    if f.status!=FactStatus.READY or not f.datum.strip() or not isinstance(f.source,str) or not f.source.strip():
        report["missing"].append(dict(fact_id=binding["fact_id"],value=None,
            obtained_by=binding["obtained_by"],reason=f.status.value))
        return None
    return f.value

def resolve_assembly(facts,model):
    r=dict(schema=SCHEMA,state="INCOMPLETE",physical_approval=False,cases={},
        missing=[],errors=[],consumed_facts=[])
    try:
        if set(model)!={"bodies","cases"} or not model["bodies"] or not model["cases"]:
            raise ValueError("Explicit nonempty body inventory and load cases required")
        bodies={};values={}
        for b in model["bodies"]:
            if set(b)!={"id","parent","mass","com"} or not isinstance(b["id"],str) or not b["id"] or b["id"] in bodies:
                raise ValueError("Invalid/duplicate body")
            bodies[b["id"]]=b
            mass=fact_value(facts,b["mass"],"kg",r);com=fact_value(facts,b["com"],"mm",r)
            if mass is not None and (isinstance(mass,bool) or not isinstance(mass,(int,float)) or not np.isfinite(mass) or mass<=0):
                raise ValueError("Mass must be a positive finite kg value")
            if com is not None: com=finite(com,(3,))
            values[b["id"]]=(mass,com)
        chains={}
        for name,b in bodies.items():
            chain=[];p=name
            while p is not None:
                if p not in bodies or p in chain: raise ValueError("Cyclic/dangling mount graph")
                chain.append(p);p=bodies[p]["parent"]
            chains[name]=chain
        accelerations={}
        for case in model["cases"]:
            if set(case)!={"id","acceleration"} or not case["id"] or case["id"] in accelerations:
                raise ValueError("Invalid/duplicate load case")
            a=fact_value(facts,case["acceleration"],"m/s2",r)
            accelerations[case["id"]]=finite(a,(3,)) if a is not None else None
        if r["missing"]: return r
        for name,a in accelerations.items():
            loads={k:dict(force_n=np.zeros(3),moment_about_origin_n_mm=np.zeros(3),contributors=[]) for k in bodies}
            for body,(mass,com) in values.items():
                force=mass*a;moment=np.cross(com,force)
                for parent in chains[body]:
                    loads[parent]["force_n"]+=force
                    loads[parent]["moment_about_origin_n_mm"]+=moment
                    loads[parent]["contributors"].append(body)
            r["cases"][name]={k:{q:(v.tolist() if isinstance(v,np.ndarray) else v) for q,v in w.items()} for k,w in loads.items()}
        r["state"]="COMPLETE"
    except (ValueError,TypeError,KeyError) as exc:
        r["state"]="INVALID";r["errors"].append(str(exc));r["cases"]={}
    return r
