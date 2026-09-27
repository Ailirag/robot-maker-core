"""Synthetic CAD/materials ONLY. Never a source of real robot dimensions or strength."""
import hashlib
from pathlib import Path
from build123d import Box,Align,Pos,export_step,export_stl
from engineering.print_package import stl_mesh
from engineering.print_strength import mesh_digest
from engineering.load_pipeline import qualification_digest
from tests.test_print_package import settings

def make_fixture(root,process=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    shape=Box(24,6,6,align=(Align.MIN,Align.MIN,Align.MIN))
    step=root/"beam.step";export_step(shape,step)
    stl=root/"beam.stl";export_stl(shape.moved(Pos(30,30,0)),stl)
    p=dict(stl_mesh(stl.read_bytes()),name="beam")
    process=process or settings()
    process=process|dict(layer_height="0.2",wall_loops="2",sparse_infill_density="15%",
        top_shell_layers="5",bottom_shell_layers="5")
    facts={"parameters":{}}
    def bind(k,v,u,provenance="CALCULATED"):
        facts["parameters"][k]=dict(value=v,unit=u,provenance=provenance,
            scope="synthetic",datum="assembly XYZ, synthetic control model",
            source="SYNTHETIC TEST FIXTURE ONLY; not bench evidence")
        return dict(fact_id=k,scope="synthetic",obtained_by="supply real qualified "+k)
    transform=[[1,0,0,30],[0,1,0,30],[0,0,1,0],[0,0,0,1]]
    sha=hashlib.sha256(step.read_bytes()).hexdigest()
    profiles=[]
    for i,(walls,infill,e,allow) in enumerate([(2,"15%",1000,1.0),(4,"45%",2000,20.)]):
        ps=dict(wall_loops=str(walls),sparse_infill_density=infill,top_shell_layers="5",bottom_shell_layers="5")
        material=dict(young_mpa=e,poisson=.3,allowable_mpa=allow,
            qualification_sha256=qualification_digest(process,ps,transform,sha))
        profiles.append(dict(settings=ps,material=bind("material"+str(i),material,"elastic_print_profile","BENCH_VALIDATED")))
    assembly=dict(bodies=[
        dict(id="beam",parent=None,mass=bind("beam_mass",.001,"kg"),com=bind("beam_com",[12,3,3],"mm")),
        dict(id="payload",parent="beam",mass=bind("payload_mass",.1,"kg"),com=bind("payload_com",[24,3,3],"mm"))],
        cases=[dict(id="gravity",acceleration=bind("gravity",[0,0,-10],"m/s2"))])
    controls={k:bind(k,v,u,"REQUIREMENT") for k,v,u in [
        ("mesh_size_mm",2,"mm"),("safety_factor",1.5,"ratio"),("zone_trigger",.65,"ratio"),
        ("max_displacement_mm",.2,"mm"),("convergence_tolerance",.25,"ratio")]}
    part=dict(id="beam",print_name="beam",step="beam.step",step_sha256=sha,print_mesh_sha256=mesh_digest(p),
        assembly_to_bed=transform,supports=[bind("support",[[0,0,0],[0,6,6]],"mm")],
        interfaces={"payload":bind("mount",[[24,0,0],[24,6,6]],"mm")},
        profiles=profiles,controls=controls,model_qualification=bind("qualified",True,"bool"))
    return dict(schema="load-zones-v1",assembly=assembly,parts=[part]),facts,[p],process
