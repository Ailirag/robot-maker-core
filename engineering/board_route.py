"""Deterministic grid router plus an independent conductor connectivity audit.

Bare wire/bridges occupy every hole of an axis-aligned run. Jumpers have only
two electrical contacts; their drawn span is NOT a physical wire path or cut
length. The builder is a heuristic, not a complete routing solver.
"""
from __future__ import annotations
from engineering.board_model import contains, point, rectangle, text

ALGORITHM = 'grid-tree-straight-elbow-jumper-v1'

def run(a,b):
    if a[0]==b[0]:
        step=1 if b[1]>=a[1] else -1
        return tuple((a[0],r) for r in range(a[1],b[1]+step,step))
    if a[1]==b[1]:
        step=1 if b[0]>=a[0] else -1
        return tuple((c,a[1]) for c in range(a[0],b[0]+step,step))
    return ()

def _inputs(pins,nets,columns,rows,keepouts):
    def fail(message): raise ValueError('BOARD_ROUTE: '+message)
    if any(type(n) is not int or not 1<=n<=200 for n in (columns,rows)): fail('grid bounds')
    grid=[0,0,columns-1,rows-1]
    if not isinstance(keepouts,(list,tuple)) or any(not rectangle(r) or not contains(grid,r[:2]) or not contains(grid,r[2:]) for r in keepouts):
        fail('invalid keepouts')
    if not isinstance(pins,dict) or not pins or not all(text(r) and isinstance(v,dict) and v for r,v in pins.items()): fail('pins required')
    if sum(len(v) for v in pins.values())>512 or len(keepouts)>256: fail('input limit: 512 pins, 256 keepouts')
    if any(not text(k) for v in pins.values() for k in v): fail('named pins required')
    ids={(r,k) for r,v in pins.items() for k in v}
    if not isinstance(nets,dict) or set(nets)!=ids or any(n is not None and not text(n) for n in nets.values()): fail('explicit net for every pin required')
    occupied={};by_net={}
    for ref,keys in sorted(pins.items()):
        for key,at in sorted(keys.items()):
            if not text(key) or not point(at) or not contains(grid,at): fail('invalid pin coordinate')
            at=tuple(at)
            if at in occupied: fail('duplicate pin hole')
            if any(contains(r,at) for r in keepouts): fail('pin in keepout')
            net=nets[ref,key];occupied[at]=net
            if net is not None: by_net.setdefault(net,set()).add(at)
    return occupied,by_net

def connect(pins,nets,*,columns,rows,keepouts=(),priority=()):
    occupied,by_net=_inputs(pins,nets,columns,rows,keepouts)
    if not isinstance(priority,(list,tuple)) or not all(text(n) for n in priority) or len(set(priority))!=len(priority):
        raise ValueError('BOARD_ROUTE: invalid net priority')
    order=[n for n in priority if n in by_net]+sorted(n for n in by_net if n not in priority)
    out=[]
    for net in order:
        holes=sorted(by_net[net]);parent={h:h for h in holes}
        def find(h):
            while parent[h]!=h:
                parent[h]=parent[parent[h]];h=parent[h]
            return h
        def free(points):
            return all((h not in occupied or occupied[h]==net) and not any(contains(r,h) for r in keepouts) for h in points)
        edges=sorted((abs(a[0]-b[0])+abs(a[1]-b[1]),a,b) for i,a in enumerate(holes) for b in holes[i+1:])
        for _,a,b in edges:
            if find(a)==find(b): continue
            parent[find(a)]=find(b)
            straight=run(a,b)
            if straight and free(straight[1:-1]):
                occupied.update({h:net for h in straight[1:-1]})
                out.append((net,a,b,'wire'));continue
            laid=False
            if not straight:
                for corner in ((a[0],b[1]),(b[0],a[1])):
                    legs=run(a,corner)[1:]+run(corner,b)[1:-1]
                    if free(legs):
                        occupied.update({h:net for h in legs})
                        out.extend(((net,a,corner,'wire'),(net,corner,b,'wire')));laid=True;break
            if not laid: out.append((net,a,b,'jumper'))
    result=check(pins,nets,out,columns=columns,rows=rows,keepouts=keepouts)
    if result['status']!='VALID': raise ValueError('BOARD_ROUTE: generated route failed independent audit '+str(result['issues']))
    return tuple(out)

def check(pins,nets,links,*,columns,rows,keepouts=()):
    """Construct the electrical contact graph from the output, never its tree."""
    issues=[]
    def issue(code,**details): issues.append(dict(code=code,**details))
    try: pads,by_net=_inputs(pins,nets,columns,rows,keepouts)
    except ValueError as exc: return dict(status='INVALID',issues=[dict(code='INPUT',detail=str(exc))],holes={})
    owners={at:{net} for at,net in pads.items()}
    graph={};seen=set()
    if not isinstance(links,(list,tuple)): return dict(status='INVALID',issues=[dict(code='INPUT',detail='links required')],holes={})
    for index,link in enumerate(links):
        if not isinstance(link,(list,tuple)) or len(link)!=4:
            issue('LINK',index=index);continue
        net,a,b,kind=link
        if not text(net) or net not in by_net:
            issue('UNKNOWN_NET',index=index);continue
        if kind not in ('wire','bridge','jumper'):
            issue('KIND',index=index);continue
        if not point(a) or not point(b):
            issue('COORDINATE',index=index);continue
        a,b=tuple(a),tuple(b)
        if a==b or any(not (0<=p[0]<columns and 0<=p[1]<rows) for p in (a,b)):
            issue('ENDPOINT',index=index);continue
        identity=(net,tuple(sorted((a,b))),kind)
        if identity in seen: issue('DUPLICATE',index=index)
        seen.add(identity)
        if kind=='jumper': contacts=(a,b)
        else:
            contacts=run(a,b)
            if not contacts: issue('BARE_DIAGONAL',index=index);continue
        for p in contacts:
            owners.setdefault(p,set()).add(net)
            if any(contains(r,p) for r in keepouts): issue('KEEPOUT',index=index,at=list(p))
            graph.setdefault((net,p),set())
        # Every consecutive bare pad is electrically joined; insulated only at ends.
        for u,v in zip(contacts,contacts[1:]):
            graph[net,u].add(v);graph[net,v].add(u)
    for at,ns in sorted(owners.items()):
        if len(ns)>1: issue('SHORT',at=list(at),nets=sorted(str(n) for n in ns))
    for net,pin_holes in sorted(by_net.items()):
        start=min(pin_holes);visited={start};queue=[start]
        while queue:
            here=queue.pop()
            for other in graph.get((net,here),()):
                if other not in visited: visited.add(other);queue.append(other)
        all_contacts={p for n,p in graph if n==net}|pin_holes
        if not all_contacts<=visited: issue('OPEN',net=net,unreached=[list(p) for p in sorted(all_contacts-visited)])
    holes={at:next(iter(ns)) for at,ns in owners.items() if len(ns)==1}
    return dict(status='INVALID' if issues else 'VALID',issues=issues,holes=holes)

def workspace(model,placements,links,*,extra_holes=None):
    """Compile a checked layout to the instruction contract. No physical claim."""
    from engineering import board_model
    from engineering.instructions import board_workspace
    model=board_model.validate(model)
    audit=board_model.audit(model,placements)
    if audit['status']!='LOGICAL_VALID': raise ValueError('BOARD_ROUTE: invalid placement '+str(audit['issues']))
    pins=board_model.pins(model,placements);nets=board_model.pin_nets(model)
    checked=check(pins,nets,links,**model['grid'],keepouts=model['keepouts'])
    if checked['status']!='VALID': raise ValueError('BOARD_ROUTE: invalid wiring '+str(checked['issues']))
    holes=checked['holes']
    for at,net in (extra_holes or {}).items():
        # Replay may retain explicitly unused pads; it cannot inject conductors.
        if at not in holes:
            if net is not None: raise ValueError('BOARD_ROUTE: unexplained extra live pad')
            holes[at]=None
        elif holes[at]!=net: raise ValueError('BOARD_ROUTE: extra pad conflicts')
    return board_workspace.from_topology(**model['grid'],pins=pins,holes=holes,links=links)

def score(links):
    return [sum(k=='jumper' for _,_,_,k in links),sum(abs(a[0]-b[0])+abs(a[1]-b[1]) for _,a,b,_ in links)]
