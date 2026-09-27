"""Run a portable cable-routing input JSON; outputs never grant physical release."""
from pathlib import Path
import argparse,hashlib,json
from engineering import cable_routing as R
from engineering import cable_groups as G

def run(source,output):
    source,output=Path(source),Path(output)
    data=json.loads(source.read_bytes())
    if data.get("schema")!="cable-job-v1":raise ValueError("CABLE_JOB_SCHEMA")
    specs=data["cables"];obstacles=data["obstacles"]
    for spec in specs:
        if spec.get("points",{}).get("value") is None and spec.get("search"):
            search=spec["search"]
            points=R.search(search["start"],search["end"],obstacles,bounds=search["bounds"],
                step=search["step_mm"],margin=search["margin_mm"])
            spec["points"]={"value":points,"obtained_by":"Deterministic bounded search; inspect route before fabrication."}
    report=R.inventory(specs,data["required_ids"],obstacles)
    bundles=data.get('bundles',[])
    if len({g['id'] for g in bundles})!=len(bundles):raise ValueError('DUPLICATE_BUNDLE')
    if bundles:
        report['bundles']=[G.assess(g,specs) for g in bundles]
        for group in report['bundles']:
            if group['status']=='SECTION_CALCULATED':continue
            report['status']='ACTION_REQUIRED'
            issue='BUNDLE_UNRESOLVED:'+group['id'];report['issues'].append(issue)
            for row in report['cables']:
                if row['id'] in group['members']:
                    row['cut_length_mm']=None;row['required_length_mm']=None
                    row['issues'].append(issue);row['status']='INCOMPLETE' if row['status']=='CALCULATED' else row['status']
    if data.get("topology_complete") is not True or data.get("unresolved_topology"):
        report["status"]="ACTION_REQUIRED"
        report["issues"].append("TOPOLOGY_UNCONFIRMED")
        report["unresolved_topology"]=data.get("unresolved_topology",[])
    report["input"]={"name":source.name,"sha256":hashlib.sha256(source.read_bytes()).hexdigest()}
    report["physical_approval"]=False
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8",newline="\n")
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input",type=Path);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    r=run(args.input,args.output)
    print(r["status"])
    return 0 if r["status"]=="CALCULATED" else 2
if __name__=="__main__":raise SystemExit(main())
