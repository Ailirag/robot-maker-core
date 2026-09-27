"""Small-strain linear elastic TET4 solver; mm, N, MPa. No release authority.

Displacements use a sparse symmetric stiffness matrix; stress is constant per
tetrahedron. Homogenized print properties MUST be qualified by the caller.
No contact, plasticity, buckling, dynamics or material inference.
"""
from __future__ import annotations
import warnings
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

def finite(value, shape=None):
    raw=np.asarray(value)
    if raw.dtype.kind not in "fiu": raise ValueError("Numeric values required")
    a=np.asarray(value,dtype=float)
    if not np.isfinite(a).all() or (shape is not None and a.shape!=shape):
        raise ValueError("Invalid finite vector/array")
    return a

def rigid_modes(points):
    out=np.zeros((len(points)*3,6))
    for i,(x,y,z) in enumerate(points):
        out[i*3:i*3+3,:3]=np.eye(3)
        out[i*3:i*3+3,3:]=[[0,z,-y],[-z,0,x],[y,-x,0]]
    return out

def distribute_wrench(points, force, moment, origin):
    """Minimum-norm rigid-interface nodal forces preserving force AND moment."""
    p=finite(points)
    if p.ndim!=2 or p.shape[1]!=3 or not len(p): raise ValueError("Empty wrench interface")
    force=finite(force,(3,));moment=finite(moment,(3,));origin=finite(origin,(3,))
    center=p.mean(axis=0)
    target=np.r_[force,moment+np.cross(origin-center,force)]
    a=rigid_modes(p-center).T
    values=np.linalg.lstsq(a,target,rcond=1e-12)[0]
    if not np.allclose(a@values,target,rtol=1e-8,atol=1e-9):
        raise ValueError("Interface cannot transmit this wrench")
    return values.reshape((-1,3))

def constitutive(young, poisson):
    if not np.isfinite([young,poisson]).all() or young<=0 or not -1<poisson<.49:
        raise ValueError("Material outside linear compressible elastic scope")
    mu=young/(2*(1+poisson));lam=young*poisson/((1+poisson)*(1-2*poisson))
    d=np.zeros((6,6));d[:3,:3]=lam
    for i in range(3): d[i,i]+=2*mu
    for i in range(3,6): d[i,i]=mu
    return d

def element(points):
    m=np.column_stack((np.ones(4),points))
    volume=abs(np.linalg.det(m))/6
    if not np.isfinite(volume) or volume<=1e-12:
        raise ValueError("Degenerate tetrahedron")
    derivatives=np.linalg.inv(m)[1:,:].T
    b=np.zeros((6,12))
    for i,(x,y,z) in enumerate(derivatives):
        b[:,3*i:3*i+3]=[[x,0,0],[0,y,0],[0,0,z],[y,x,0],[0,z,y],[z,0,x]]
    return volume,b

def solve(nodes, tetrahedra, forces, fixed_nodes, *, young_mpa, poisson):
    n=finite(nodes);f=finite(forces,n.shape)
    raw=np.asarray(tetrahedra)
    if n.ndim!=2 or n.shape[1]!=3 or raw.ndim!=2 or raw.shape[1]!=4 or not len(raw):
        raise ValueError("Expected nodes and TET4 cells")
    if raw.dtype.kind not in "iu" or raw.min()<0 or raw.max()>=len(n):
        raise ValueError("Invalid tetrahedron index")
    t=raw.astype(int);fixed=np.asarray(fixed_nodes)
    if fixed.dtype.kind not in "iu" or fixed.ndim!=1 or not len(fixed) or fixed.min()<0 or fixed.max()>=len(n):
        raise ValueError("Invalid restraints")
    if len(set(map(tuple,np.sort(t,axis=1))))!=len(t):
        raise ValueError("Duplicate tetrahedra")
    if len(np.unique(t))!=len(n): raise ValueError("Unused mesh nodes")
    edges=np.array([(int(c[0]),int(i)) for c in t for i in c[1:]])
    graph=coo_matrix((np.ones(len(edges)),(edges[:,0],edges[:,1])),shape=(len(n),len(n))).tocsr()
    if connected_components(graph,directed=False,return_labels=False)!=1:
        raise ValueError("Disconnected analysis mesh")
    if np.linalg.matrix_rank(rigid_modes(n[fixed]-n.mean(axis=0)),tol=1e-8)<6:
        raise ValueError("Restraints leave rigid body modes")
    e=np.broadcast_to(finite(young_mpa),(len(t),));nu=np.broadcast_to(finite(poisson),(len(t),))
    rows=[];cols=[];entries=[];elems=[]
    for c,young,p in zip(t,e,nu,strict=True):
        v,b=element(n[c]);d=constitutive(young,p);k=v*(b.T@d@b)
        dofs=(c[:,None]*3+np.arange(3)).ravel()
        rows.extend(np.repeat(dofs,12));cols.extend(np.tile(dofs,12));entries.extend(k.ravel())
        elems.append((v,b,d,dofs))
    k=coo_matrix((entries,(rows,cols)),shape=(len(n)*3,len(n)*3)).tocsc()
    constrained=np.unique((fixed[:,None]*3+np.arange(3)).ravel())
    free=np.setdiff1d(np.arange(len(n)*3),constrained)
    if not len(free): raise ValueError("No free degrees of freedom")
    reduced=k[free][:,free]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            lu=splu(reduced)
            piv=np.abs(lu.U.diagonal())
            if piv.min()<max(piv.max(),1)*1e-12: raise ValueError("Singular stiffness")
            u=np.zeros(len(n)*3);u[free]=lu.solve(f.ravel()[free])
    except (RuntimeError,Warning) as exc:
        raise ValueError("Singular stiffness or invalid mesh") from exc
    residual=k@u-f.ravel()
    if not np.isfinite(u).all() or np.linalg.norm(residual[free])>1e-7*max(np.linalg.norm(f),1):
        raise ValueError("Equilibrium residual too large")
    reactions=np.zeros(len(n)*3);reactions[constrained]=residual[constrained]
    stress=[];vm=[];principal=[];volumes=[]
    for v,b,d,dofs in elems:
        s=d@b@u[dofs];stress.append(s.tolist());volumes.append(v)
        sx,sy,sz,txy,tyz,tzx=s
        vm.append(float(np.sqrt(max(0,.5*((sx-sy)**2+(sy-sz)**2+(sz-sx)**2)+3*(txy*txy+tyz*tyz+tzx*tzx)))))
        principal.append(np.linalg.eigvalsh([[sx,txy,tzx],[txy,sy,tyz],[tzx,tyz,sz]]).tolist())
    balance=f+reactions.reshape((-1,3))
    return dict(displacement_mm=u.reshape((-1,3)).tolist(),stress_mpa=stress,
        principal_mpa=principal,von_mises_mpa=vm,element_volume_mm3=volumes,
        reaction_n=reactions.reshape((-1,3)).tolist(),
        force_residual_n=float(np.linalg.norm(balance.sum(axis=0))),
        moment_residual_n_mm=float(np.linalg.norm(np.cross(n-n.mean(axis=0),balance).sum(axis=0))),
        strain_energy_n_mm=float(.5*u@(k@u)),
        max_displacement_mm=float(np.linalg.norm(u.reshape((-1,3)),axis=1).max()))

def zones(nodes,tetrahedra,values,*,threshold):
    """Find connected above-threshold cells; return spatial envelopes, no manual zones."""
    n=finite(nodes);t=np.asarray(tetrahedra,int);v=finite(values,(len(t),))
    if not np.isfinite(threshold) or threshold<=0: raise ValueError("Positive zone threshold required")
    active=set(np.flatnonzero(v>=threshold).tolist());by_node={}
    for i in active:
        for node in t[i]: by_node.setdefault(int(node),set()).add(i)
    output=[]
    while active:
        seed=min(active);active.remove(seed);group=[seed];todo=[seed]
        while todo:
            i=todo.pop()
            neighbours=set().union(*(by_node[int(x)] for x in t[i])) & active
            active-=neighbours;todo.extend(sorted(neighbours));group.extend(sorted(neighbours))
        points=n[t[group]].reshape((-1,3))
        output.append(dict(elements=sorted(group),bounds_mm=[points.min(axis=0).tolist(),points.max(axis=0).tolist()],
            peak=float(v[group].max())))
    return output
