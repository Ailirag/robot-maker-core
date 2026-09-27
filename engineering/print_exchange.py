"""Independent FreeCAD reopen of a print bundle; no geometry authoring or repair."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from engineering.print_calibration import number

PROBE = r'''
import os,json,sys,Part,FreeCAD
from pathlib import Path
d=Path(os.environ["ENGINEERING_CALIBRATION_BUNDLE"])
r=json.loads((d/"REPORT.json").read_text(encoding="utf-8"))
def bounds(s):
 b=s.optimalBoundingBox(False,False)
 return [[b.XMin,b.YMin,b.ZMin],[b.XMax,b.YMax,b.ZMax]]
def read(name):
 s=Part.read(str(d/name))
 vv=[]
 for tolerance in (0.004,0.001):
  v,t=s.cleaned().tessellate(tolerance)
  vv.append(sum(v[a].dot(v[b].cross(v[c])) for a,b,c in t)/6)
 b=bounds(s)
 return s,{"valid":s.isValid(),"solids":len(s.Solids),"bounds_mm":b,
           "size_mm":[b[1][i]-b[0][i] for i in range(3)],"mesh_volumes_mm3":vv,
           "default_volume_mm3":s.Volume}
job,j=read("PRINT_JOB.step")
shapes={};parts={}
for row in r["parts"]:
 shapes[row["id"]],parts[row["id"]]=read(row["id"]+".step")
probes=[shapes[p["part"]].isInside(FreeCAD.Vector(*p["point"]),1e-7,True)
        for p in r["interface_checks"].get("probe_specs",[])]
extra=[n for n in r["artifacts"] if n.endswith(".step") and n!="PRINT_JOB.step" and n[:-5] not in parts]
out={"job":j,"parts":parts,"probes":probes,
     "extra_steps_valid":all(Part.read(str(d/n)).isValid() for n in extra),
     "FreeCAD":FreeCAD.Version(),"python":sys.version}
print("CALIBRATION_EXCHANGE_JSON="+json.dumps(out))
'''


def assess(report, actual):
    errors=[]
    def check(name, row, count, volume, sizes=None, bounds=None):
        if row.get("valid") is not True or row.get("solids")!=count:
            errors.append("BREP: "+name)
        volumes=[number(v,positive=True) for v in row["mesh_volumes_mm3"]]
        if len(volumes)!=2 or abs(volumes[-1]-volume)>max(1e-4,volume*1e-5) or abs(volumes[-1]-volumes[0])>max(1e-4,volume*1e-5):
            errors.append("VOLUME: fine mesh or convergence differs: "+name)
        if sizes is not None:
            if len(row["size_mm"])!=3 or any(abs(number(a)-b)>1e-5 for a,b in zip(row["size_mm"],sizes)):
                errors.append("BBOX: "+name)
        if bounds is not None:
            if len(row["bounds_mm"])!=2 or any(len(v)!=3 for v in row["bounds_mm"]):
                errors.append("BBOX: "+name)
            elif any(abs(number(a)-b)>1e-5 for aa,bb in zip(row["bounds_mm"],bounds) for a,b in zip(aa,bb)):
                errors.append("BBOX: "+name)
    try:
        check("job",actual["job"],report["solid_count"],report["total_volume_mm3"],bounds=report["checks"]["bounds_mm"])
        expected={p["id"] for p in report["parts"]}
        if set(actual["parts"])!=expected: errors.append("PART_SET: independent result differs")
        for p in report["parts"]:
            check(p["id"],actual["parts"][p["id"]],1,p["volume_mm3"],sizes=p["print_size_mm"])
        targets=[p["inside"] for p in report["interface_checks"].get("probe_specs",[])]
        if len(actual["probes"])!=len(targets) or any(a is not b for a,b in zip(actual["probes"],targets)):
            errors.append("INTERFACE: independent solid probes differ")
        if actual["extra_steps_valid"] is not True: errors.append("BREP: section export")
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        errors.append("EXCHANGE: incomplete or invalid result: "+str(exc))
    return errors


def exchange(bundle, freecadcmd, receipt_path):
    from engineering.print_geometry import verify_bundle
    bundle=Path(bundle).resolve()
    receipt_path=Path(receipt_path)
    if receipt_path.exists(): raise ValueError("EXISTS: preserve earlier verification")
    issues=verify_bundle(bundle)
    if issues: raise ValueError(issues)
    report=json.loads((bundle/"REPORT.json").read_text(encoding="utf-8"))
    env=dict(os.environ)
    for key in ("PYTHONHOME","PYTHONPATH","VIRTUAL_ENV"): env.pop(key,None)
    env["ENGINEERING_CALIBRATION_BUNDLE"]=str(bundle)
    result=subprocess.run([str(freecadcmd),"-c",PROBE],env=env,capture_output=True,
                          text=True,encoding="utf-8",errors="replace",timeout=180)
    lines=[line.split("=",1)[1] for line in result.stdout.splitlines() if line.startswith("CALIBRATION_EXCHANGE_JSON=")]
    if result.returncode or len(lines)!=1:
        raise ValueError("FREECAD: independent reader failed: "+result.stdout+result.stderr)
    actual=json.loads(lines[0])
    errors=assess(report,actual)
    record={"schema":"print-exchange-evidence-v1","status":"FAIL" if errors else "PASS","errors":errors,
            "manifest_sha256":hashlib.sha256((bundle/"MANIFEST.json").read_bytes()).hexdigest(),
            "verifier_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "actual":actual,"method":"FreeCAD B-Rep validity, optimal geometric bounds, solid probes; volume from two mesh refinements 0.004/0.001 mm vs adaptive author B-Rep integral; relative acceptance 1e-5.",
            "finding":"Default non-adaptive volume and loose spline bounds are diagnostic only, not acceptance values.",
            "physical_approval":False}
    receipt_path.parent.mkdir(parents=True,exist_ok=True)
    with receipt_path.open("x",encoding="utf-8",newline="\n") as stream:
        stream.write(json.dumps(record,ensure_ascii=False,indent=2)+"\n")
    if errors: raise ValueError(errors)
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle",type=Path)
    parser.add_argument("--freecadcmd",required=True)
    parser.add_argument("--receipt",type=Path,required=True)
    args=parser.parse_args()
    print(exchange(args.bundle,args.freecadcmd,args.receipt)["status"])

if __name__=="__main__": main()
