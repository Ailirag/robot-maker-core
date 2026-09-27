"""Bounded deterministic placement enumeration with explicit outcome semantics.

Optimality, when exhaustive, is ONLY over the declared grid/rotation domains
and the deterministic routing heuristic's score. It is not global routing
optimality or a physical assembly/power approval. Manhattan span is not cut length.
"""
from __future__ import annotations
from copy import deepcopy
from engineering import board_model as model, board_route as route

SCHEMA = 'perfboard-search-result-v1'
ALGORITHM = 'grid-enumeration-v1'

def solve(value,facts,*,max_nodes,priority=()):
    if type(max_nodes) is not int or max_nodes<1:
        raise ValueError('BOARD_SEARCH: positive integer max_nodes required')
    if not isinstance(priority,(list,tuple)) or not all(model.text(n) for n in priority) or len(set(priority))!=len(priority):
        raise ValueError('BOARD_SEARCH: unique net priority required')
    value=model.validate(value);physical=model.physical_inputs(value,facts)
    result=dict(schema=SCHEMA,algorithm=ALGORITHM,router=route.ALGORITHM,
        input_sha256=model.input_hash(value,physical),settings=dict(max_nodes=max_nodes,priority=list(priority)),
        status=physical['status'],visited=0,exhaustive=False,optimal_within_model=False,
        optimality_scope='declared placements scored by fixed routing heuristic',
        physical_release=False,best=None,issues=physical['issues'])
    if physical['status']!='READY': return result
    # Fixed parts first, then stable reference order. Candidates are lazy: a large
    # domain cannot bypass the node limit while constructing candidate arrays.
    refs=sorted(value['components'],key=lambda r:(value['components'][r]['fixed'] is None,r))
    placements={};limited=False;best_key=None
    def domain(part):
        if part['fixed'] is not None:
            yield part['fixed'];return
        for x in range(value['grid']['columns']):
            for y in range(value['grid']['rows']):
                for rotation in part['rotations']:
                    yield dict(at=[x,y],rotation=rotation)
    def visit(depth):
        nonlocal limited,best_key
        if depth==len(refs):
            pins=model._pins(value,placements)
            links=route.connect(pins,model.pin_nets(value),**value['grid'],keepouts=value['keepouts'],priority=priority)
            score=route.score(links)
            key=(tuple(score),tuple((r,*placements[r]['at'],placements[r]['rotation']) for r in sorted(placements)))
            if best_key is None or key<best_key:
                best_key=key
                result['best']=dict(placements=deepcopy(placements),
                    links=[[n,list(a),list(b),k] for n,a,b,k in links],score=score)
            return
        ref=refs[depth]
        for pos in domain(value['components'][ref]):
            if result['visited']>=max_nodes:
                limited=True;return
            result['visited']+=1;placements[ref]=pos
            if not model.placement_issues(value,placements,physical['values'],partial=True): visit(depth+1)
            del placements[ref]
            if limited: return
    visit(0)
    result['exhaustive']=not limited
    result['status']='SEARCH_LIMIT' if limited else ('SOLVED' if result['best'] else 'INFEASIBLE')
    result['optimal_within_model']=bool(result['best']) and not limited
    if result['best']:
        # Public independent audit of the returned result, including complete ref set.
        checked=model.audit(value,result['best']['placements'],facts)
        if checked['status']!='VALID': raise ValueError('BOARD_SEARCH: result failed independent audit')
    return result

def main(argv=None):
    import argparse,json
    from pathlib import Path
    from engineering.facts import FactManifest
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--facts',type=Path,required=True)
    parser.add_argument('--max-nodes',type=int,required=True)
    parser.add_argument('--priority',action='append',default=[])
    args=parser.parse_args(argv)
    try:
        value=json.loads(args.model.read_text(encoding='utf-8'))
        result=solve(value,FactManifest.from_path(args.facts),max_nodes=args.max_nodes,priority=args.priority)
    except (OSError,ValueError) as exc:
        print(json.dumps(dict(status='INVALID_INPUT',error=str(exc)),ensure_ascii=False))
        return 1
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True,indent=2))
    return {'SOLVED':0,'SEARCH_LIMIT':2,'INPUTS_INCOMPLETE':3,'INFEASIBLE':4,'INVALID':1}[result['status']]

if __name__=='__main__': raise SystemExit(main())
