"""Bounded local analyze/slice/mass feedback. Never contacts a printer."""
from __future__ import annotations
import copy,hashlib,json,subprocess
from pathlib import Path
import numpy as np
from engineering.facts import FactManifest
from engineering.load_pipeline import prepare_analysis,render_report,transform_points
from engineering.print_modifiers import inspect_project,compare_projects
from engineering.slicer_mass import project_masses

def apply_slice_facts(facts,spec,mass_report):
    result=copy.deepcopy(facts)
    bodies={b["id"]:b for b in spec["assembly"]["bodies"]}
    for p in spec["parts"]:
        if "exempt_reason" in p: continue
        body=bodies[p["id"]];mass=mass_report["objects"][p["print_name"]]
        inverse=np.linalg.inv(np.array(p["assembly_to_bed"],float))
        com=transform_points([mass["com_bed_mm"]],inverse)[0].tolist()
        for key,value,unit in (("mass",mass["mass_g"]/1000,"kg"),("com",com,"mm")):
            binding=body[key]
            result.setdefault("parameters",{})[binding["fact_id"]]=dict(value=value,unit=unit,
                provenance="CALCULATED",scope=binding["scope"],datum="assembly XYZ from declared rigid bed transform",
                source="sliced 3MF SHA-256 "+mass_report["source_sha256"]+"; object "+p["print_name"],
                obtained_by=binding["obtained_by"])
    return result

def mass_stable(before,after):
    a=before["objects"];b=after["objects"]
    return set(a)==set(b) and all(
        abs(a[k]["mass_g"]-b[k]["mass_g"])<=max(1e-7,a[k]["mass_g"]*1e-6)
        and np.allclose(a[k]["com_bed_mm"],b[k]["com_bed_mm"],rtol=0,atol=1e-5) for k in a)

def validate_native_receipt(path):
    receipt=json.loads(Path(path).read_text(encoding="utf-8"))
    plates=receipt.get("sliced_plates")
    if receipt.get("return_code")!=0 or receipt.get("error_string")!="Success.":
        raise ValueError("Native slicer receipt is not successful")
    if not isinstance(plates,list) or len(plates)!=1 or plates[0].get("warning_message")!="":
        raise ValueError("Unresolved native slicing warnings or missing plate")
    return receipt

def slice_local(path,slicer,directory):
    directory=Path(directory);directory.mkdir()
    target=directory/"SLICED.3mf"
    cmd=[str(Path(slicer).resolve()),"--datadir",str(directory/"data"),"--arrange","0","--orient","0",
        "--slice","1","--debug","2","--outputdir",str(directory),"--export-3mf",target.name,str(Path(path).resolve())]
    r=subprocess.run(cmd,capture_output=True,timeout=180,
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    (directory/"slicer.log").write_bytes(r.stdout+r.stderr)
    if r.returncode or not target.is_file(): raise ValueError("Slicer did not produce a successful project")
    native=validate_native_receipt(directory/"result.json")
    expected=inspect_project(path);actual=inspect_project(target)
    compare_projects(expected,actual)
    if not actual["gcodes"]: raise ValueError("No sliced toolpath")
    receipt=dict(argv=cmd,exit_code=r.returncode,input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),geometry_and_modifiers_preserved=True,
        warnings_xml=actual["warnings"],native_receipt_sha256=hashlib.sha256((directory/"result.json").read_bytes()).hexdigest())
    (directory/"RECEIPT.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    return target

def run(model_path,facts_path,project_path,destination,*,slicer,gmsh_pythonpath=None,max_iterations=3):
    model_path=Path(model_path);facts_path=Path(facts_path);project_path=Path(project_path);destination=Path(destination)
    if destination.exists(): raise ValueError("Choose a new destination")
    if type(max_iterations) is not int or not 1<=max_iterations<=8: raise ValueError("Bounded iteration count 1..8 required")
    inputs={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in (model_path,facts_path,project_path)}
    spec=json.loads(model_path.read_text(encoding="utf-8"));facts=json.loads(facts_path.read_text(encoding="utf-8"))
    original=inspect_project(project_path);destination.mkdir(parents=True)
    result=dict(state="INCOMPLETE",physical_approval=False,project=None,iterations=[],inputs=inputs,errors=[])
    report=None;verified_sliced=None
    producer_files=[Path(__file__).with_name(name) for name in ("load_runner.py","load_pipeline.py","load_solver.py","load_mesh.py","load_paths.py","slicer_mass.py","print_modifiers.py","print_settings.py","print_package.py")]
    result["producer_sha256"]={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in producer_files}
    try:
        baseline=project_path if original["gcodes"] else slice_local(project_path,slicer,destination/"baseline_slice")
        masses=project_masses(baseline);facts=apply_slice_facts(facts,spec,masses)
        last_sha=None;last_sliced=None
        for index in range(1,max_iterations+1):
            step=destination/("iteration_"+str(index));step.mkdir()
            used=step/"FACTS_USED.json";used.write_text(json.dumps(facts,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
            report=prepare_analysis(spec,FactManifest(facts),original["parts"],original["settings"],model_path.parent,
                gmsh_pythonpath=gmsh_pythonpath)
            raw=report.pop("project");report["project"]="PREPARED.3mf" if raw else None
            (step/"REPORT.json").write_text(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2)+"\n",encoding="utf-8",newline="\n")
            (step/"REPORT.html").write_text(render_report(report),encoding="utf-8",newline="\n")
            if not raw:
                result["state"]=report["state"];result["errors"]=report["errors"];break
            prepared=step/"PREPARED.3mf";prepared.write_bytes(raw)
            sha=hashlib.sha256(raw).hexdigest()
            sliced=last_sliced if sha==last_sha else slice_local(prepared,slicer,step/"slice")
            updated=project_masses(sliced)
            stable=mass_stable(masses,updated)
            result["iterations"].append(dict(index=index,prepared_sha256=sha,
                mass_stable=stable,slice_reused=sha==last_sha,sliced=str(sliced),
                masses=updated,report=str(step/"REPORT.json")))
            if stable:
                result["state"]="VERIFIED_CALCULATION";result["project"]="REINFORCED.3mf"
                verified_sliced=sliced
                result["output_sha256"]=hashlib.sha256(sliced.read_bytes()).hexdigest()
                break
            facts=apply_slice_facts(facts,spec,updated);masses=updated
            last_sha=sha;last_sliced=sliced
        else: result["state"]="MASS_FEEDBACK_NOT_CONVERGED"
        for p,sha in inputs.items():
            if hashlib.sha256(Path(p).read_bytes()).hexdigest()!=sha:
                raise ValueError("Input changed during calculation: "+p)
        for p in producer_files:
            if hashlib.sha256(p.read_bytes()).hexdigest()!=result["producer_sha256"][p.name]:
                raise ValueError("Producer changed during calculation: "+p.name)
        if verified_sliced is not None:
            (destination/"REINFORCED.3mf").write_bytes(verified_sliced.read_bytes())
    except (ValueError,KeyError,TypeError,OSError,subprocess.TimeoutExpired) as exc:
        result["state"]="INVALID";result["project"]=None;result["errors"].append(str(exc))
    (destination/"RESULT.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    if report is None:
        report=dict(parts={},missing=[],errors=[],next_actions=["Read engineering/LOAD_ZONE_WORKFLOW.md and resolve the reported input error."])
    if report:
        report["state"]=result["state"];report["errors"]+=result["errors"]
        (destination/"REPORT.html").write_text(render_report(report),encoding="utf-8",newline="\n")
    return result

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("model","facts","project","destination","slicer"): parser.add_argument("--"+name,required=True,type=Path)
    parser.add_argument("--gmsh-pythonpath",type=Path)
    parser.add_argument("--max-iterations",type=int,default=3)
    args=parser.parse_args()
    result=run(args.model,args.facts,args.project,args.destination,slicer=args.slicer,
        gmsh_pythonpath=args.gmsh_pythonpath,max_iterations=args.max_iterations)
    print(json.dumps(dict(state=result["state"],project=result["project"],errors=result["errors"])))
    return 0 if result["project"] else 2

if __name__=="__main__": raise SystemExit(main())
