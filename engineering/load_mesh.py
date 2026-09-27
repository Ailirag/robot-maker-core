"""Isolated Gmsh STEP meshing. CAD remains the supplied build123d-authored STEP."""
from __future__ import annotations
import hashlib,json,math,os,subprocess,sys,tempfile
from pathlib import Path

def mesh_step(path,size_mm,gmsh_pythonpath=None):
    path=Path(path).resolve()
    if not path.is_file() or not isinstance(size_mm,(int,float)) or not math.isfinite(size_mm) or size_mm<=0:
        raise ValueError("Existing STEP and positive mesh size required")
    env=os.environ.copy()
    roots=[str(Path(__file__).resolve().parent.parent)]
    if gmsh_pythonpath: roots.append(str(Path(gmsh_pythonpath).resolve()))
    env["PYTHONPATH"]=os.pathsep.join(roots)
    # Isolate Gmsh's OCCT DLLs from build123d's OCCT DLLs.
    with tempfile.TemporaryDirectory(prefix="load-mesh-") as tmp:
        out=Path(tmp)/"mesh.json"
        cmd=[sys.executable,"-m","engineering.load_mesh","--worker",str(path),str(size_mm),str(out)]
        r=subprocess.run(cmd,capture_output=True,env=env,timeout=180,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if r.returncode: raise ValueError("Mesh worker failed: "+(r.stdout+r.stderr).decode("utf-8",errors="replace")[-1500:])
        result=json.loads(out.read_text(encoding="utf-8"))
    return result

def worker(path,size,out):
    import gmsh
    if gmsh.__version__!="4.15.2": raise ValueError("Qualified mesher version is 4.15.2")
    source=Path(path).read_bytes()
    gmsh.initialize(["gmsh","-nopopup"])
    try:
        gmsh.option.setNumber("General.Terminal",0)
        gmsh.option.setNumber("General.NumThreads",1)
        gmsh.option.setNumber("Mesh.RandomSeed",42)
        gmsh.model.add("structural")
        shapes=gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.synchronize()
        if len(gmsh.model.getEntities(3))!=1: raise ValueError("One solid required per analysis")
        gmsh.option.setNumber("Mesh.MeshSizeMin",size)
        gmsh.option.setNumber("Mesh.MeshSizeMax",size)
        gmsh.option.setNumber("Mesh.ElementOrder",1)
        gmsh.model.mesh.generate(3)
        tags,coords,_=gmsh.model.mesh.getNodes()
        coordinates={int(k):[float(v) for v in coords[3*i:3*i+3]] for i,k in enumerate(tags)}
        types,_,node_lists=gmsh.model.mesh.getElements(3)
        if list(types)!=[4]: raise ValueError("Expected linear tetrahedra")
        conn=[int(x) for x in node_lists[0]]
        used=sorted(set(conn));indices={k:i for i,k in enumerate(used)}
        tets=[[indices[k] for k in conn[i:i+4]] for i in range(0,len(conn),4)]
        result=dict(schema="tetra-mesh-v1",mesher="gmsh",version=gmsh.__version__,
            step_sha256=hashlib.sha256(source).hexdigest(),size_mm=size,
            nodes=[coordinates[k] for k in used],tetrahedra=tets,
            cad_volume_mm3=sum(gmsh.model.occ.getMass(3,k) for _,k in gmsh.model.getEntities(3)))
        if Path(path).read_bytes()!=source: raise ValueError("STEP changed during meshing")
        Path(out).write_text(json.dumps(result,allow_nan=False),encoding="utf-8")
    finally: gmsh.finalize()

if __name__=="__main__":
    if len(sys.argv)!=5 or sys.argv[1]!="--worker": raise SystemExit("Internal --worker STEP SIZE OUTPUT")
    worker(Path(sys.argv[2]),float(sys.argv[3]),Path(sys.argv[4]))
