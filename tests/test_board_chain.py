"""End-to-end board relocation: catalogue -> search -> images, tables and operations."""
from copy import deepcopy
import json
import pytest
from engineering import board_catalogue as C, board_pipeline as P
from engineering.instructions import board_recipe as R, board_workspace as W
from engineering.facts import FactManifest

def fixture():
    values={'pitch':1.,'margin':.5,'width':4.,'height':3.,'clearance':0.,'ceiling':2.,
            'envelope':[-.2,-.2,0,.2,1.2,1.],'fit':True}
    facts=FactManifest({'parameters':{k:dict(value=v,unit='bool' if k=='fit' else 'mm',
        provenance='SYNTHETIC',datum='pin 1 / grid origin',scope='sample',source='test data') for k,v in values.items()}})
    def q(k): return dict(fact_id=k,unit='bool' if k=='fit' else 'mm',allowed_provenance=['SYNTHETIC'],require_datum=True,scope='sample')
    cat=dict(schema=C.SCHEMA,revision='1',footprints={'pair':dict(revision='1',obtained_by='Synthetic pin map',
        pins={'1':[0,0],'2':[0,1]},physical=dict(envelope_mm=q('envelope'),pin_fit=q('fit'),pitch_mm=q('pitch')))},
        parts={'connector':dict(revision='1',label='Fixture connector',footprint='pair'),
               'resistor':dict(revision='1',label='Fixture resistor',footprint='pair')})
    design=dict(schema=C.DESIGN_SCHEMA,revision='1',grid=dict(columns=4,rows=3),keepouts=[],
        physical={k:q(v) for k,v in dict(pitch_mm='pitch',edge_x_mm='margin',edge_y_mm='margin',width_mm='width',
            height_mm='height',clearance_mm='clearance',max_height_mm='ceiling',keepout_review='fit').items()},
        components={r:dict(part=p,nets={'1':'A','2':'B'},rotations=[0,90],fixed=dict(at=[0,0],rotation=0) if r=='J_A' else None,
            region=None) for r,p in [('J_A','connector'),('R_A','resistor')]})
    def op(kind,refs,action,select=None):
        return dict(kind=kind,refs=refs,pins=None,select=select,title=kind,action=action,material='Fixture only',
                    check='Inspect {{pin:R_A.1}} and {{pin:R_A.2}}; orientation {{direction:R_A:up}}.')
    recipe=dict(schema=R.SCHEMA,revision='1',source='Synthetic procedure',groups={
        'place':dict(requires=[],title='Place parts',operations=[op('place',['J_A','R_A'],'Match {{contacts:R_A}}.')]),
        'wire':dict(requires=['place'],title='Connect',operations=[op('connect',[],'Join {{from}} to {{to}} on {{net}}.',
            dict(kinds=['wire','jumper','bridge'],priority=[]))])})
    return cat,design,facts,recipe

def solved(c,d,f): return P.build(c,d,f,mode='search',max_nodes=1000)

def test_relocation_updates_every_coordinate_consumer():
    c,d,f,r=fixture()
    a=solved(c,d,f); va=R.views(a,r,title='Fixture')
    assert a['calculation']['status']=='SOLVED'
    d['components']['R_A'].update(fixed={'at':[3,2],'rotation':90})
    b=solved(c,d,f); vb=R.views(b,r,title='Fixture')
    assert P.verify(c,d,f,b)==b
    pins={p['pin']:p['at'] for p in vb['layout']['pins'] if p['ref']=='R_A'}
    assert pins=={'1':[3,2],'2':[2,2]}
    assert '(3,2)' in vb['plan']['operations'][0]['action']
    assert '(2,2)' in vb['plan']['operations'][0]['action']
    assert 'вправо' in vb['plan']['operations'][0]['check']
    for key in ('svg','solder_svg','solder_table'):
        assert va[key]!=vb[key]
        assert 'R_A.1' in vb[key] and '(3,2)' in vb[key]
    assert a['result_sha256']!=b['result_sha256']
    assert {op['links'][0] for op in vb['plan']['operations'] if op['kind']=='connect'}=={l['id'] for l in b['layout']['links']}
    html=W.render(dict(ref='SYN',title='Fixture',**vb))
    assert '(3,2)' in html and 'board-print' in html
    with pytest.raises(ValueError,match='STALE_CHAIN_INPUTS'): P.verify(c,d,f,a)

def test_catalogue_reuses_one_footprint_and_is_deterministic():
    c,d,f,r=fixture()
    assert len(C.compile(c,d)['model']['footprints'])==1
    before=solved(c,d,f)
    c=json.loads(json.dumps(c,sort_keys=True));d=json.loads(json.dumps(d,sort_keys=True))
    assert solved(c,d,f)==before
    r['groups']=dict(reversed(list(r['groups'].items())))
    assert R.compile(before,r)['operations'][0]['kind']=='place'

@pytest.mark.parametrize('bad',['part','pin','footprint','revision','datum','scope'])
def test_catalogue_and_fact_rejections(bad):
    c,d,f,r=fixture()
    if bad=='part': d['components']['R_A']['part']='missing'
    if bad=='pin': del d['components']['R_A']['nets']['2']
    if bad=='footprint': c['parts']['resistor']['footprint']='missing'
    if bad=='revision': c['revision']=''
    if bad in ('datum','scope'):
        f._parameters['pitch'][bad]=None
        result=solved(c,d,f)
        assert result['layout'] is None
        assert result['calculation']['status']=='INPUTS_INCOMPLETE'
        with pytest.raises(ValueError,match='no usable placement'): R.compile(result,r)
    else:
        with pytest.raises(ValueError): solved(c,d,f)

@pytest.mark.parametrize('bad',['cycle','dependency','pin','duplicate-place','missing-place','literal','token','duplicate-link','missing-link'])
def test_recipe_rejects_incomplete_or_stale_actions(bad):
    c,d,f,r=fixture();a=solved(c,d,f)
    place=r['groups']['place']['operations'][0];wire=r['groups']['wire']['operations'][0]
    if bad=='cycle': r['groups']['place']['requires']=['wire']
    if bad=='dependency': r['groups']['place']['requires']=['absent']
    if bad=='pin': place['action']='Inspect {{pin:R_A.9}}.'
    if bad=='duplicate-place': r['groups']['place']['operations'].append(deepcopy(place))
    if bad=='missing-place': place['refs']=['J_A']
    if bad=='literal': place['action']='Put in (1,0).'
    if bad=='token': place['action']='Inspect {{unknown}}.'
    if bad=='duplicate-link': r['groups']['wire']['operations'].append(deepcopy(wire))
    if bad=='missing-link': wire['select']['kinds']=['bridge']
    with pytest.raises(ValueError): R.compile(a,r)

def test_tamper_and_stale_facts_fail_even_with_valid_json():
    c,d,f,r=fixture();a=solved(c,d,f)
    x=deepcopy(a);x['layout']['pins'][0]['at']=[1,1]
    with pytest.raises(ValueError,match='hash'): P.inspect(x)
    x=P.seal({k:v for k,v in x.items() if k!='result_sha256'})
    with pytest.raises(ValueError,match='pins differ'): P.inspect(x)
    f._parameters['pitch']['value']=1.1
    with pytest.raises(ValueError,match='STALE_CHAIN_INPUTS'): P.verify(c,d,f,a)

def test_replay_is_explicit_and_baseline_cannot_move_silently():
    c,d,f,r=fixture();a=solved(c,d,f)
    for ref,pos in a['calculation']['best']['placements'].items(): d['components'][ref]['fixed']=pos
    model=C.compile(c,d)['model']
    base=dict(model_sha256=C.digest(model),links=a['calculation']['best']['links'],extra_holes=[])
    replay=P.build(c,d,f,mode='replay',max_nodes=100,baseline=base)
    assert replay['layout'] is not None and replay['physical_release'] is False
    d['components']['R_A']['fixed']={'at':[3,2],'rotation':90}
    with pytest.raises(ValueError,match='STALE_BASELINE'): P.build(c,d,f,mode='replay',max_nodes=100,baseline=base)
    with pytest.raises(ValueError): P.build(c,d,f,mode='search',max_nodes=100,baseline=base)

def test_search_limit_does_not_publish_a_partial_best():
    c,d,f,r=fixture()
    result=P.build(c,d,f,mode='search',max_nodes=1)
    assert result['calculation']['status']=='SEARCH_LIMIT' and result['layout'] is None

def test_record_carries_the_authoring_inputs_and_renderer_rejects_mixed_surfaces():
    c,d,f,r=fixture();record=solved(c,d,f)
    assert record['catalogue']==c and record['design']==d
    board=dict(ref='SYN',title='Fixture',**R.views(record,r,title='Fixture'))
    board['svg']='<svg>old placement</svg>'
    with pytest.raises(ValueError,match='CHAIN_VIEW'):
        W.render(board)

def test_saved_replay_must_match_current_route_baseline():
    c,d,f,r=fixture();a=solved(c,d,f)
    for ref,pos in a['calculation']['best']['placements'].items(): d['components'][ref]['fixed']=pos
    base=dict(model_sha256=C.digest(C.compile(c,d)['model']),links=a['calculation']['best']['links'],extra_holes=[])
    record=P.build(c,d,f,mode='replay',max_nodes=100,baseline=base)
    changed=deepcopy(base);changed['links'][0][3]='jumper'
    with pytest.raises(ValueError,match='BASELINE'):
        P.verify(c,d,f,record,baseline=changed)

def test_catalogue_preserves_explicit_provision_without_claiming_installation():
    c,d,f,r=fixture()
    c['parts']['resistor']['provision']={'status':'ordered','source':'explicit procurement record'}
    record=solved(c,d,f)
    row=next(row for row in record['bom'] if row[0]=='R_A')
    assert row[2]=='ordered' and 'procurement record' in row[3]
