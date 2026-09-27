"""Independent checks: linear elasticity patch, equilibrium and force transfer."""
import copy
import numpy as np
import pytest
from engineering.load_solver import solve, distribute_wrench, zones
from engineering.load_paths import resolve_assembly
from engineering.facts import FactManifest

def grid(nx=4, ny=2, nz=2, size=(20,4,4)):
    nodes=[[i*size[0]/nx,j*size[1]/ny,k*size[2]/nz]
           for i in range(nx+1) for j in range(ny+1) for k in range(nz+1)]
    def ix(i,j,k): return (i*(ny+1)+j)*(nz+1)+k
    cells=[]
    for i in range(nx):
      for j in range(ny):
       for k in range(nz):
        v=[ix(i+di,j+dj,k+dk) for di,dj,dk in
           [(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)]]
        for t in [(0,1,2,6),(0,2,3,6),(0,3,7,6),(0,7,4,6),(0,4,5,6),(0,5,1,6)]:
         cells.append([v[x] for x in t])
    return np.array(nodes,float),np.array(cells,int)

def loading(nodes, force=(16,0,0), moment=(0,0,0)):
    fixed=np.flatnonzero(nodes[:,0]==0).tolist()
    end=np.flatnonzero(nodes[:,0]==nodes[:,0].max())
    f=np.zeros_like(nodes)
    f[end]=distribute_wrench(nodes[end],force,moment,nodes[end].mean(axis=0))
    return fixed,f

def test_axial_patch_matches_closed_form_and_energy():
    n,t=grid();fixed,f=loading(n);f[:]=0
    # Constant traction: each boundary triangle contributes area/3 per node.
    from collections import Counter
    from itertools import combinations
    faces=Counter(tuple(sorted(face)) for cell in t for face in combinations(cell,3))
    for face,count in faces.items():
        p=n[list(face)]
        if count==1 and (p[:,0]==20).all():
            area=np.linalg.norm(np.cross(p[1]-p[0],p[2]-p[0]))/2
            f[list(face),0]+=area/3
    r=solve(n,t,f,fixed,young_mpa=1000,poisson=0)
    assert np.array(r["displacement_mm"])[:,0]==pytest.approx(n[:,0]/1000,abs=1e-9)
    assert np.array(r["von_mises_mpa"])==pytest.approx(np.ones(len(t)),abs=1e-8)
    assert r["strain_energy_n_mm"]==pytest.approx(.5*16*.02)
    assert r["force_residual_n"]<1e-8
    assert r["moment_residual_n_mm"]<1e-8

def test_wrench_preserves_force_and_eccentric_moment():
    n,_=grid();points=n[n[:,0]==20]
    force=np.array([0,0,-2.]);moment=np.array([3.,4.,5.]);origin=np.array([23,6,7])
    f=distribute_wrench(points,force,moment,origin)
    assert f.sum(axis=0)==pytest.approx(force)
    assert np.cross(points-origin,f).sum(axis=0)==pytest.approx(moment)

def test_impossible_torque_at_one_point_is_refused():
    with pytest.raises(ValueError,match="wrench"):
        distribute_wrench([[0,0,0]],[0,0,1],[0,1,0],[0,0,0])

def test_bending_zones_are_found_near_mount_without_manual_zone_coordinates():
    n,t=grid(nx=12,ny=3,nz=3);fixed,f=loading(n,force=(0,0,-1))
    r=solve(n,t,f,fixed,young_mpa=1000,poisson=.3)
    stress=np.array(r["von_mises_mpa"])
    centers=n[t].mean(axis=1)
    assert stress[centers[:,0]<4].mean()>stress[centers[:,0]>16].mean()*2
    regions=zones(n,t,stress,threshold=float(stress.max())*.65)
    assert regions and any(b["bounds_mm"][0][0]<4 for b in regions)
    assert r["force_residual_n"]<1e-7 and r["moment_residual_n_mm"]<1e-6

def test_linear_superposition():
    n,t=grid();fixed,f=loading(n)
    r=solve(n,t,f,fixed,young_mpa=1000,poisson=.2)
    rr=solve(n,t,f*3,fixed,young_mpa=1000,poisson=.2)
    assert np.array(rr["displacement_mm"])==pytest.approx(np.array(r["displacement_mm"])*3)

@pytest.mark.parametrize("bad",["floating","degenerate","disconnected","nan","negative_material"])
def test_invalid_mesh_and_restraints_fail_closed(bad):
    n,t=grid();fixed,f=loading(n);e=1000
    if bad=="floating": fixed=[0]
    if bad=="degenerate": t[0]=[0,0,0,0]
    if bad=="disconnected":
        n=np.vstack([n, n+100]);t=np.vstack([t,t+len(n)//2]);f=np.vstack([f,f*0])
    if bad=="nan": f[0,0]=float("nan")
    if bad=="negative_material": e=-1
    with pytest.raises(ValueError):
        solve(n,t,f,fixed,young_mpa=e,poisson=.3)

def assembly_data():
    facts={"parameters":{}}
    def bind(key,value,unit):
        facts["parameters"][key]=dict(value=value,unit=unit,provenance="CALCULATED",
            source="independent synthetic fixture",datum="assembly XYZ",scope="sample")
        return dict(fact_id=key,scope="sample",obtained_by="measure "+key)
    bodies=[
      dict(id="base",parent=None,mass=bind("m0",.1,"kg"),com=bind("c0",[0,0,0],"mm")),
      dict(id="tray",parent="base",mass=bind("m1",.2,"kg"),com=bind("c1",[10,0,0],"mm")),
      dict(id="battery",parent="tray",mass=bind("m2",.5,"kg"),com=bind("c2",[20,0,0],"mm"))]
    case=dict(id="gravity",acceleration=bind("a",[0,0,-10],"m/s2"))
    return facts,dict(bodies=bodies,cases=[case])

def test_mass_tree_transfers_subtree_loads_without_double_counting():
    facts,model=assembly_data();r=resolve_assembly(FactManifest(facts),model)
    assert r["state"]=="COMPLETE"
    w=r["cases"]["gravity"]["base"]
    assert w["force_n"]==pytest.approx([0,0,-8])
    assert w["moment_about_origin_n_mm"]==pytest.approx([0,120,0])
    assert r["cases"]["gravity"]["tray"]["force_n"]==pytest.approx([0,0,-7])

def test_unknown_mass_is_null_and_has_obtained_by():
    facts,model=assembly_data();facts["parameters"]["m2"]["value"]=None
    r=resolve_assembly(FactManifest(facts),model)
    assert r["state"]=="INCOMPLETE" and not r["cases"]
    assert r["missing"][0]["value"] is None and r["missing"][0]["obtained_by"]

def test_cyclic_or_dangling_mount_graph_is_refused():
    facts,model=assembly_data();model["bodies"][0]["parent"]="battery"
    assert resolve_assembly(FactManifest(facts),model)["state"]=="INVALID"

def test_rigid_rotation_does_not_change_von_mises():
    n,t=grid();fixed,f=loading(n,force=(0,0,-1))
    q=np.array([[0,-1,0],[0,0,-1],[1,0,0.]])
    a=solve(n,t,f,fixed,young_mpa=1300,poisson=.3)
    b=solve(n@q.T,t,f@q.T,fixed,young_mpa=1300,poisson=.3)
    assert b["von_mises_mpa"]==pytest.approx(a["von_mises_mpa"],rel=1e-8)
    assert np.array(b["displacement_mm"])==pytest.approx(np.array(a["displacement_mm"])@q.T,abs=1e-9)
