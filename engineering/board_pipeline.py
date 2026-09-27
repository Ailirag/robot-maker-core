"""One checked placement record for all board consumers. Never grants physical use."""
from copy import deepcopy
from engineering import board_catalogue as catalog, board_model as model, board_route as route, board_search as search

SCHEMA = 'perfboard-chain-v1'

def binding(compiled, facts):
    return catalog.digest(dict(catalogue=compiled['catalogue_sha256'],design=compiled['design_sha256'],
        model=model.input_hash(compiled['model'],model.physical_inputs(compiled['model'],facts))))

def seal(record):
    return dict(record,result_sha256=catalog.digest(record))

def inspect(record):
    """Check internal coherence independently, even for a decoded downloaded record."""
    model.fields(record,('schema','mode','inputs_sha256','catalogue','design','baseline','model','bom','calculation','layout','result_sha256','physical_release'),'chain')
    if record['schema']!=SCHEMA or record['mode'] not in ('replay','search') or record['physical_release'] is not False:
        model.fail('chain schema/mode')
    if record['result_sha256']!=catalog.digest({k:v for k,v in record.items() if k!='result_sha256'}):
        model.fail('chain result hash')
    compiled=catalog.compile(record['catalogue'],record['design'])
    if compiled['model']!=record['model'] or compiled['bom']!=record['bom']:
        model.fail('chain catalogue/model mismatch')
    value=model.validate(record['model']); result=record['calculation']
    if result['physical_release'] is not False or result['algorithm']!=search.ALGORITHM or result['router']!=route.ALGORITHM:
        model.fail('calculation algorithm/release mismatch')
    if record['mode']=='search' and record['baseline'] is not None:
        model.fail('search carries replay baseline')
    if record['layout'] is not None:
        best=result['best'] if record['mode']=='search' else None
        positions=best['placements'] if best else model.fixed_placements(value)
        if record['mode']=='search' and (result['status']!='SOLVED' or best is None):
            model.fail('search result is not solved')
        audit=model.audit(value,positions)
        if audit['status']!='LOGICAL_VALID': model.fail('chain placement '+str(audit['issues']))
        layout=record['layout']
        expected_pins=model.pins(value,positions)
        actual={(p['ref'],p['pin']):tuple(p['at']) for p in layout['pins']}
        if actual!={(r,p):at for r,ps in expected_pins.items() for p,at in ps.items()}:
            model.fail('chain pins differ')
        raw_links=[(l['net'],l['from'],l['to'],l['kind']) for l in layout['links']]
        if best is not None and catalog.digest(raw_links)!=catalog.digest(best['links']):
            model.fail('chain conductors differ')
        holes={tuple(h['at']):h['net'] for h in layout['holes']}
        if record['mode']=='replay':
            baseline=record['baseline']
            model.fields(baseline,('model_sha256','links','extra_holes'),'baseline')
            if baseline['model_sha256']!=catalog.digest(value) or catalog.digest(raw_links)!=catalog.digest(baseline['links']):
                model.fail('STALE_BASELINE')
            holes={tuple(h['at']):h['net'] for h in baseline['extra_holes']}
        rebuilt=route.workspace(value,positions,raw_links,extra_holes=holes)
        if catalog.digest(rebuilt)!=catalog.digest(layout): model.fail('chain layout differs')
    return record

def build(catalogue, design, facts, *, mode, max_nodes, priority=(), baseline=None):
    compiled=catalog.compile(catalogue,design); value=compiled['model']
    if mode not in ('replay','search'): model.fail('explicit chain mode required')
    if mode=='search' and baseline is not None: model.fail('search cannot consume replay baseline')
    result=search.solve(value,facts,max_nodes=max_nodes,priority=priority)
    layout=None
    if mode=='replay':
        if baseline is None: model.fail('replay baseline required')
        model.fields(baseline,('model_sha256','links','extra_holes'),'baseline')
        if baseline['model_sha256']!=catalog.digest(value): model.fail('STALE_BASELINE')
        positions=model.fixed_placements(value)
        audit=model.audit(value,positions)
        if audit['status']!='LOGICAL_VALID': model.fail('invalid replay placement')
        extra={tuple(h['at']):h['net'] for h in baseline['extra_holes']}
        if len(extra)!=len(baseline['extra_holes']): model.fail('duplicate baseline hole')
        layout=route.workspace(value,positions,baseline['links'],extra_holes=extra)
    elif result['status']=='SOLVED':
        layout=route.workspace(value,result['best']['placements'],result['best']['links'])
    return inspect(seal(dict(schema=SCHEMA,mode=mode,inputs_sha256=binding(compiled,facts),catalogue=deepcopy(catalogue),design=deepcopy(design),baseline=deepcopy(baseline),model=value,
        bom=compiled['bom'],calculation=result,layout=layout,physical_release=False)))

def verify(catalogue, design, facts, record, *, baseline=None):
    inspect(record)
    compiled=catalog.compile(catalogue,design)
    if catalog.digest(record['baseline'])!=catalog.digest(baseline):
        model.fail('STALE_BASELINE: current baseline required for saved replay')
    if binding(compiled,facts)!=record['inputs_sha256'] or compiled['model']!=record['model'] or compiled['bom']!=record['bom']:
        model.fail('STALE_CHAIN_INPUTS')
    result=record['calculation']
    if result['input_sha256']!=model.input_hash(compiled['model'],model.physical_inputs(compiled['model'],facts)):
        model.fail('STALE_CALCULATION')
    if record['mode']=='search' and record['layout'] is not None:
        if model.audit(compiled['model'],result['best']['placements'],facts)['status']!='VALID':
            model.fail('invalid physical placement')
    return record

def main(argv=None):
    import argparse,json
    from pathlib import Path
    from engineering.facts import FactManifest
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('catalogue','design','facts'): parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--mode',choices=('replay','search'),required=True)
    parser.add_argument('--max-nodes',type=int,required=True)
    args=parser.parse_args(argv)
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    result=build(read(args.catalogue),read(args.design),FactManifest.from_path(args.facts),
        mode=args.mode,max_nodes=args.max_nodes,baseline=read(args.baseline) if args.baseline else None)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0 if result['layout'] is not None else 2

if __name__=='__main__': raise SystemExit(main())
