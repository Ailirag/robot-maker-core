"""Fact-bound hybrid workflow: declared mounting model -> FEM zones -> local 3MF.

The model supplies a reviewable mechanical interpretation. This module never
infers supports/materials from appearance or treats its result as physical approval.
"""
from __future__ import annotations
import copy,hashlib,html,json,math,tempfile
from pathlib import Path
import numpy as np
from engineering.load_paths import resolve_assembly,fact_value
from engineering.load_solver import finite,solve,zones,distribute_wrench
from engineering.load_mesh import mesh_step
from engineering.print_strength import mesh_digest
from engineering import print_modifiers
from engineering.print_settings import validate_part_settings,PART_SETTING_KEYS

SCHEMA="load-zones-v1"
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def qualification_digest(settings,profile,transform,step_sha256):
    return digest(dict(settings=settings,profile=profile,assembly_to_bed=transform,step_sha256=step_sha256))

def transform_points(points,matrix):
    m=finite(matrix,(4,4))
    if not np.allclose(m[3],[0,0,0,1]) or not np.allclose(m[:3,:3].T@m[:3,:3],np.eye(3),atol=1e-8) or not np.isclose(np.linalg.det(m[:3,:3]),1):
        raise ValueError("Assembly-to-bed must be a rigid, right-handed transform")
    return finite(points)@m[:3,:3].T+m[:3,3]

def surface_nodes(nodes,tets,bounds):
    from collections import Counter
    from itertools import combinations
    box=finite(bounds,(2,3))
    if (box[1]<box[0]).any(): raise ValueError("Invalid mount bounds")
    faces=Counter(tuple(sorted(f)) for t in tets for f in combinations(t,3))
    surface=sorted({i for f,count in faces.items() if count==1 for i in f})
    ids=np.array([i for i in surface if np.all(nodes[i]>=box[0]-1e-7) and np.all(nodes[i]<=box[1]+1e-7)],int)
    if len(ids)<3: raise ValueError("Mount patch selects fewer than three boundary nodes")
    return ids

def material_profile(facts,profile,settings,transform,sha,report):
    if set(profile)!={"settings","material"} or set(profile["settings"])!=set(PART_SETTING_KEYS):
        raise ValueError("Each profile declares all four shell/infill settings and a material fact")
    validate_part_settings(profile["settings"])
    material=fact_value(facts,profile["material"],"elastic_print_profile",report,calibrated=True)
    if material is None: return None
    keys={"young_mpa","poisson","allowable_mpa","qualification_sha256"}
    if not isinstance(material,dict) or set(material)!=keys: raise ValueError("Invalid calibrated material profile")
    if material["qualification_sha256"]!=qualification_digest(settings,profile["settings"],transform,sha):
        report["missing"].append(dict(value=None,obtained_by=profile["material"]["obtained_by"],
            reason="Calibration identity differs from geometry/orientation/process"))
        return None
    if any(isinstance(material[k],bool) or not isinstance(material[k],(int,float)) for k in ("young_mpa","poisson","allowable_mpa")):
        raise ValueError("Numeric material properties required")
    from engineering.load_solver import constitutive
    constitutive(material["young_mpa"],material["poisson"])
    if not isinstance(material["allowable_mpa"],(int,float)) or not math.isfinite(material["allowable_mpa"]) or material["allowable_mpa"]<=0:
        raise ValueError("Positive calibrated elastic stress limit required")
    return material

def _prepare_part(spec,facts,settings,part,root,report):
    required={"id","print_name","step","step_sha256","print_mesh_sha256","assembly_to_bed",
              "supports","interfaces","profiles","model_qualification","controls"}
    if set(spec)!=required: raise ValueError("Missing/unknown structural part fields")
    path=(root/spec["step"]).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file(): raise ValueError("STEP must exist inside input root")
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if spec["step_sha256"]!=sha or spec["print_mesh_sha256"]!=mesh_digest(part):
        raise ValueError("Stale STEP/print geometry binding")
    transform_points([[0,0,0]],spec["assembly_to_bed"])
    qualified=fact_value(facts,spec["model_qualification"],"bool",report)
    if qualified is not True:
        report["missing"].append(dict(value=None,obtained_by=spec["model_qualification"]["obtained_by"],
            reason="Model interpretation has not been qualified"))
    if not spec["supports"] or not spec["profiles"]: raise ValueError("Supports and profiles required")
    supports=[fact_value(facts,b,"mm",report) for b in spec["supports"]]
    interfaces={k:fact_value(facts,b,"mm",report) for k,b in spec["interfaces"].items()}
    materials=[material_profile(facts,p,settings,spec["assembly_to_bed"],sha,report) for p in spec["profiles"]]
    controls=spec["controls"]
    units=dict(mesh_size_mm="mm",safety_factor="ratio",zone_trigger="ratio",
               max_displacement_mm="mm",convergence_tolerance="ratio")
    if set(controls)!=set(units): raise ValueError("Explicit numerical controls required")
    c={k:fact_value(facts,controls[k],unit,report) for k,unit in units.items()}
    if report["missing"]: return None
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in c.values()):
        raise ValueError("Positive finite controls required")
    if c["safety_factor"]<1 or not 0<c["zone_trigger"]<=1 or not 0<c["convergence_tolerance"]<=.25:
        raise ValueError("Invalid safety, zone or convergence limits")
    # STEP-to-print check: only rigid placement is permitted. Compare full mesh
    # containment plus volume; a matching bounding box alone is insufficient.
    from build123d import import_step,Vector
    shape=import_step(path)
    if len(shape.solids())!=1 or not shape.is_valid: raise ValueError("Invalid STEP solid")
    inverse=np.linalg.inv(finite(spec["assembly_to_bed"],(4,4)))
    vertices=transform_points(part["vertices"],inverse)
    from engineering.print_package import mesh_report
    volume=mesh_report(part)["volume_mm3"]
    if abs(volume-shape.volume)>max(.01,shape.volume*.01):
        raise ValueError("STEP/print volume mismatch")
    # Every mesh vertex must lie on the STEP boundary, within export tolerance.
    if max(shape.distance_to(Vector(*p)) for p in vertices)>.03:
        raise ValueError("Print mesh does not match transformed STEP")
    return dict(path=path,shape=shape,supports=supports,interfaces=interfaces,
        materials=materials,controls=c,spec=spec)

def analyze_mesh(prepared,mesh,assembly,model,profile_regions):
    spec=prepared["spec"];c=prepared["controls"]
    n=finite(mesh["nodes"]);t=np.asarray(mesh["tetrahedra"],int)
    fixed=sorted(set().union(*(set(surface_nodes(n,t,b)) for b in prepared["supports"])))
    interfaces={k:surface_nodes(n,t,b) for k,b in prepared["interfaces"].items()}
    children=[b["id"] for b in model["bodies"] if b["parent"]==spec["id"]]
    if set(children)!=set(interfaces): raise ValueError("Every child needs exactly one declared mounting interface")
    centers=n[t].mean(axis=1);levels=np.zeros(len(t),int)
    for region in profile_regions:
        bounds=np.array(region["bounds_mm"])
        mask=np.all(centers>=bounds[0]-1e-8,axis=1)&np.all(centers<=bounds[1]+1e-8,axis=1)
        levels[mask]=np.maximum(levels[mask],region["profile"])
    mats=prepared["materials"]
    e=np.array([mats[i]["young_mpa"] for i in levels]);nu=np.array([mats[i]["poisson"] for i in levels])
    allow=np.array([mats[i]["allowable_mpa"] for i in levels])
    utilization=np.zeros(len(t));energy=np.zeros(len(t));maxdisp=0.;results={}
    for name,loads in assembly["cases"].items():
        own_force=np.array(loads[spec["id"]]["force_n"],float)
        own_moment=np.array(loads[spec["id"]]["moment_about_origin_n_mm"],float)
        f=np.zeros_like(n)
        for child,ids in interfaces.items():
            w=loads[child];force=np.array(w["force_n"]);moment=np.array(w["moment_about_origin_n_mm"])
            f[ids]+=distribute_wrench(n[ids],force,moment,[0,0,0])
            own_force-=force;own_moment-=moment
        # Effective distributed body loading preserves the specified COM wrench.
        f+=distribute_wrench(n,own_force,own_moment,[0,0,0])
        result=solve(n,t,f*c["safety_factor"],fixed,young_mpa=e,poisson=nu)
        principal=np.max(np.abs(result["principal_mpa"]),axis=1)
        demand=np.maximum(principal,np.array(result["von_mises_mpa"]))
        utilization=np.maximum(utilization,demand/allow)
        energy=np.maximum(energy,np.array(result["von_mises_mpa"])**2/e)
        maxdisp=max(maxdisp,result["max_displacement_mm"])
        results[name]=dict(max_displacement_mm=result["max_displacement_mm"],
            max_stress_mpa=float(demand.max()),strain_energy_n_mm=result["strain_energy_n_mm"],
            force_residual_n=result["force_residual_n"],moment_residual_n_mm=result["moment_residual_n_mm"])
    return dict(nodes=n,tetrahedra=t,utilization=utilization,energy=energy,
        max_displacement_mm=maxdisp,cases=results,levels=levels)

def modifier_mesh(shape,region,transform,settings,name):
    from build123d import Box,Align,Pos,export_stl
    from engineering.print_package import stl_mesh
    bounds=np.array(region["bounds_mm"]);size=bounds[1]-bounds[0]
    if np.any(size<=0): raise ValueError("Degenerate modifier")
    box=Box(*size,align=(Align.MIN,Align.MIN,Align.MIN)).moved(Pos(*bounds[0]))
    intersection=shape & box
    if not intersection.is_valid or intersection.volume<=1e-8:
        raise ValueError("Modifier does not intersect parent solid")
    # A box is a parameter volume, not new part geometry; the slicer clips it
    # against the parent. Keeping the box preserves the analysis region exactly.
    with tempfile.TemporaryDirectory(prefix="load-zone-") as tmp:
        p=Path(tmp)/"zone.stl";export_stl(box,p);mesh=stl_mesh(p.read_bytes())
    mesh["vertices"]=transform_points(mesh["vertices"],transform).tolist()
    return dict(mesh,name=name,print_settings=dict(settings),intersection_volume_mm3=intersection.volume)

def prepare_analysis(spec,facts,parts,settings,root,*,gmsh_pythonpath=None):
    root=Path(root)
    r=dict(schema=SCHEMA,state="INCOMPLETE",physical_approval=False,project=None,
        parts={},missing=[],errors=[],consumed_facts=[],next_actions=[])
    try:
        if set(spec)!={"schema","assembly","parts"} or spec["schema"]!=SCHEMA:
            raise ValueError("Invalid load analysis contract")
        assembly=resolve_assembly(facts,spec["assembly"]);r["assembly_loads"]=assembly
        r["missing"]+=assembly["missing"];r["consumed_facts"]+=assembly["consumed_facts"]
        if assembly["state"]=="INVALID": raise ValueError("; ".join(assembly["errors"]))
        if assembly["state"]!="COMPLETE": return _pending(r)
        names={p["name"]:p for p in parts}
        entries={p["print_name"]:p for p in spec["parts"]}
        if len(names)!=len(parts) or len(entries)!=len(spec["parts"]) or set(names)!=set(entries) or not parts:
            raise ValueError("Every print part needs one analysis or documented exemption")
        prepared={};out=copy.deepcopy(parts)
        for name,p in entries.items():
            if set(p)=={"print_name","exempt_reason"} and isinstance(p["exempt_reason"],str) and p["exempt_reason"].strip():
                r["parts"][name]=dict(state="EXEMPT",reason=p["exempt_reason"]);continue
            if p.get("id") not in next(iter(assembly["cases"].values())):
                raise ValueError("Analyzed part absent from assembly inventory")
            prepared[name]=_prepare_part(p,facts,settings,names[name],root,r)
        if r["missing"]: return _pending(r)
        for name,p in prepared.items():
            c=p["controls"];entry=p["spec"]
            coarse=mesh_step(p["path"],c["mesh_size_mm"],gmsh_pythonpath)
            regions=[];a=analyze_mesh(p,coarse,assembly,spec["assembly"],regions)
            baseline={k:v for k,v in a.items() if k in {"cases","max_displacement_mm"}}
            for level in range(1,len(p["materials"])):
                values=a["utilization"].copy();threshold=c["zone_trigger"]
                if a["max_displacement_mm"]>c["max_displacement_mm"] and a["energy"].max()>0:
                    values=np.maximum(values,a["energy"]/a["energy"].max())
                found=zones(a["nodes"],a["tetrahedra"],values,threshold=threshold)
                if not found: break
                regions.extend(dict(bounds_mm=z["bounds_mm"],profile=level) for z in found)
                a=analyze_mesh(p,coarse,assembly,spec["assembly"],regions)
            fine=mesh_step(p["path"],c["mesh_size_mm"]*.65,gmsh_pythonpath)
            b=analyze_mesh(p,fine,assembly,spec["assembly"],regions)
            # Check volume and mesh refinement; a finer picture alone is not convergence.
            def volume(mesh):
                n=np.array(mesh["nodes"]);t=np.array(mesh["tetrahedra"]);v=n[t]
                return float(np.abs(np.linalg.det(np.stack((v[:,1]-v[:,0],v[:,2]-v[:,0],v[:,3]-v[:,0]),axis=2))).sum()/6)
            for m in (coarse,fine):
                if abs(volume(m)-p["shape"].volume)>max(.01,p["shape"].volume*.01):
                    raise ValueError("Analysis mesh volume differs from CAD")
            changes=[]
            for case in a["cases"]:
                for key in ("max_displacement_mm","strain_energy_n_mm","max_stress_mpa"):
                    x=a["cases"][case][key];y=b["cases"][case][key]
                    changes.append(abs(x-y)/max(abs(y),1e-9))
            convergence=max(changes,default=0)
            ok=convergence<=c["convergence_tolerance"] and b["utilization"].max()<=1 and b["max_displacement_mm"]<=c["max_displacement_mm"]
            p_report=dict(state="CALCULATION_PASS" if ok else "REVIEW_REQUIRED",
                baseline=baseline,cases=b["cases"],max_utilization=float(b["utilization"].max()),
                convergence_change=convergence,zones=regions,mesh_nodes=len(b["nodes"]),
                mesh_elements=len(b["tetrahedra"]),step_sha256=entry["step_sha256"],
                samples=[dict(xyz=xyz.tolist(),utilization=float(u)) for xyz,u in
                         zip(b["nodes"][b["tetrahedra"]].mean(axis=1),b["utilization"])])
            r["parts"][name]=p_report
            if ok:
                target=next(x for x in out if x["name"]==name)
                target["print_settings"]=dict(entry["profiles"][0]["settings"])
                target["modifiers"]=[modifier_mesh(p["shape"],z,entry["assembly_to_bed"],
                    entry["profiles"][z["profile"]]["settings"],"load_zone_"+str(i+1)) for i,z in enumerate(regions)]
        if not all(p["state"] in {"EXEMPT","CALCULATION_PASS"} for p in r["parts"].values()):
            r["state"]="REVIEW_REQUIRED";return _pending(r)
        raw=print_modifiers.project_bytes(out,settings,"Calculated load zones")
        r["state"]="CALCULATION_PASS";r["prepared_sha256"]=hashlib.sha256(raw).hexdigest()
        r["project"]=raw
        r["next_actions"]=["Slice the prepared project; compare geometry and modifier settings.",
            "Import new per-part slice masses and repeat analysis if consumed masses changed.",
            "Physical acceptance remains a separate authority."]
        return r
    except (ValueError,TypeError,KeyError,OSError,RuntimeError) as exc:
        r["state"]="INVALID";r["errors"].append(str(exc));r["project"]=None
        return _pending(r)

def _pending(report):
    report["next_actions"]=["Read engineering/LOAD_ZONE_WORKFLOW.md.",
        "Resolve missing facts from CAD, exact sliced files or recorded measurements; retain null + obtained_by otherwise.",
        "Review unsupported load paths, mounting patches or mesh convergence; revise the model and rerun."]
    return report

def render_report(report):
    rows=[];plots=[]
    for name,p in report["parts"].items():
        rows.append("<tr><td>"+html.escape(name)+"</td><td>"+html.escape(p["state"])+"</td><td>"+
                    html.escape(str(p.get("max_utilization","вЂ”")))+"</td></tr>")
        samples=p.get("samples",[])
        if samples:
            points=np.array([s["xyz"] for s in samples]);lo=points.min(axis=0);span=np.maximum(points.max(axis=0)-lo,1)
            circles=[]
            for s in samples:
                x,y,_=(np.array(s["xyz"])-lo)/span
                color="hsl("+str(max(0,120*(1-min(s["utilization"],1))))+",85%,40%)"
                circles.append(f'<circle cx="{20+560*x:.2f}" cy="{280-260*y:.2f}" r="2" fill="{color}"><title>{s["utilization"]:.3f}</title></circle>')
            plots.append("<h2>"+html.escape(name)+"</h2><p>РџСЂРѕРµРєС†РёСЏ XY. Р¦РІРµС‚ вЂ” РґРѕР»СЏ РґРѕРїСѓСЃС‚РёРјРѕРіРѕ РЅР°РїСЂСЏР¶РµРЅРёСЏ; РєСЂР°СЃРЅС‹Р№ в‰Ґ 1.</p><svg viewBox='0 0 600 300'>"+"".join(circles)+"</svg>")
    return "<!doctype html><html lang='ru'><meta charset='utf-8'><title>РќР°РіСЂСѓР¶РµРЅРЅС‹Рµ Р·РѕРЅС‹</title><style>body{font:17px system-ui;max-width:1000px;margin:32px auto;padding:16px}td,th{padding:8px;border:1px solid #ccc}svg{width:100%;background:#f4f6fa}pre{white-space:pre-wrap}</style><h1>Р Р°СЃС‡С‘С‚ РЅР°РіСЂСѓР¶РµРЅРЅС‹С… Р·РѕРЅ</h1><p>"+html.escape(report["state"])+"</p><p>Р›РёРЅРµР№РЅР°СЏ СЃС‚Р°С‚РёС‡РµСЃРєР°СЏ РѕС†РµРЅРєР°. Р¤РёР·РёС‡РµСЃРєРёР№ РґРѕРїСѓСЃРє РЅРµ РІС‹РґР°РЅ.</p><table><tr><th>Р”РµС‚Р°Р»СЊ</th><th>РЎРѕСЃС‚РѕСЏРЅРёРµ</th><th>РњР°РєСЃРёРјР°Р»СЊРЅР°СЏ РґРѕР»СЏ РґРѕРїСѓСЃС‚РёРјРѕРіРѕ РЅР°РїСЂСЏР¶РµРЅРёСЏ</th></tr>"+"".join(rows)+"</table>"+"".join(plots)+"<h2>РЎР»РµРґСѓСЋС‰РёРµ РґРµР№СЃС‚РІРёСЏ</h2><pre>"+html.escape("\n".join(report["next_actions"]))+"</pre><h2>РќРµРґРѕСЃС‚Р°СЋС‰РёРµ РґР°РЅРЅС‹Рµ Рё РѕС€РёР±РєРё</h2><pre>"+html.escape(json.dumps(dict(missing=report["missing"],errors=report["errors"]),ensure_ascii=False,indent=2))+"</pre></html>"

def main():
    import argparse
    from engineering.facts import FactManifest
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model",type=Path,required=True)
    parser.add_argument("--facts",type=Path,required=True)
    parser.add_argument("--project",type=Path,required=True)
    parser.add_argument("--destination",type=Path,required=True)
    parser.add_argument("--gmsh-pythonpath",type=Path)
    args=parser.parse_args()
    if args.destination.exists(): parser.error("Choose a new destination")
    source=print_modifiers.inspect_project(args.project)
    spec=json.loads(args.model.read_text(encoding="utf-8"))
    report=prepare_analysis(spec,FactManifest.from_path(args.facts),source["parts"],source["settings"],
        args.model.parent,gmsh_pythonpath=args.gmsh_pythonpath)
    raw=report.pop("project");report["project"]="PREPARED.3mf" if raw else None
    report["inputs"]={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.model,args.facts,args.project)}
    args.destination.mkdir(parents=True)
    if raw: (args.destination/"PREPARED.3mf").write_bytes(raw)
    (args.destination/"REPORT.json").write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    (args.destination/"REPORT.html").write_text(render_report(report),encoding="utf-8",newline="\n")
    print(json.dumps(dict(state=report["state"],report=str(args.destination/"REPORT.html"),project=report["project"])))
    return 0 if raw else 2

if __name__=="__main__": raise SystemExit(main())
