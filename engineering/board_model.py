"""Grid assignments and explicit physical inputs; no placement grants release.

An envelope includes the body AND its required service/lead access, expressed
from local pin origin, with z measured above the board. Orthogonal rotations
preserve named pins. A recorded fit refers to this footprint on this pitch.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import math
from engineering.facts import FactManifest

SCHEMA = 'perfboard-model-v1'
BOARD_FIELDS = ('pitch_mm','edge_x_mm','edge_y_mm','width_mm','height_mm','clearance_mm','max_height_mm','keepout_review')
FOOTPRINT_FIELDS = ('envelope_mm','pin_fit','pitch_mm')

def fail(message):
    raise ValueError('BOARD_MODEL: '+message)

def text(value):
    return isinstance(value,str) and bool(value.strip())

def number(value):
    return type(value) in (int,float) and math.isfinite(value)

def fields(value, names, path):
    if not isinstance(value,dict) or set(value)!=set(names): fail(path+': unexpected or missing fields')

def point(value):
    return isinstance(value,(list,tuple)) and len(value)==2 and all(type(x) is int for x in value)

def rectangle(value):
    return isinstance(value,list) and len(value)==4 and all(type(x) is int for x in value) and value[0]<=value[2] and value[1]<=value[3]

def contains(rect, at):
    return rect[0]<=at[0]<=rect[2] and rect[1]<=at[1]<=rect[3]

def rotate(at, angle):
    x,y=at
    return {0:(x,y),90:(-y,x),180:(-x,-y),270:(y,-x)}[angle]

def _query(value, unit, path):
    if not isinstance(value,dict): fail(path+': fact query or explicit null required')
    if 'value' in value:
        fields(value,('value','unit','obtained_by'),path)
        if value['value'] is not None or not text(value['obtained_by']): fail(path+': unknown must be null with obtained_by')
    else:
        fields(value,('fact_id','unit','allowed_provenance','require_datum','scope'),path)
        if not text(value['fact_id']) or not text(value['scope']) or value['require_datum'] is not True:
            fail(path+': explicit identity, scope and datum requirement required')
        p=value['allowed_provenance']
        if not isinstance(p,list) or not p or not all(text(x) for x in p) or len(set(p))!=len(p):
            fail(path+': explicit provenance required')
        value['allowed_provenance']=sorted(p)
    if value['unit']!=unit: fail(path+': expected '+unit)

def validate(value):
    """Return a canonical JSON-compatible copy, rejecting unsupported fields."""
    value=deepcopy(value)
    fields(value,('schema','grid','physical','keepouts','footprints','components'),'model')
    if value['schema']!=SCHEMA: fail('unsupported schema')
    fields(value['grid'],('columns','rows'),'grid')
    if any(type(v) is not int or not 1<=v<=200 for v in value['grid'].values()): fail('grid bounds')
    grid=[0,0,value['grid']['columns']-1,value['grid']['rows']-1]
    if not isinstance(value['keepouts'],list): fail('keepouts must be a list')
    for rect in value['keepouts']:
        if not rectangle(rect) or not contains(grid,rect[:2]) or not contains(grid,rect[2:]): fail('invalid keepout')
    value['keepouts']=sorted(value['keepouts'])
    fields(value['physical'],BOARD_FIELDS,'physical')
    for k,v in value['physical'].items(): _query(v,'bool' if k=='keepout_review' else 'mm','physical.'+k)
    fps=value['footprints']; parts=value['components']
    if not isinstance(fps,dict) or not fps or not all(text(k) for k in fps): fail('footprint library required')
    if not isinstance(parts,dict) or not parts or not all(text(k) for k in parts): fail('component instances required')
    if len(parts)>128 or len(value['keepouts'])>256: fail('input limit: 128 components, 256 keepouts')
    for name,fp in fps.items():
        fields(fp,('pins','physical'),'footprint '+name)
        if not isinstance(fp['pins'],dict) or not fp['pins'] or not all(text(k) and point(p) for k,p in fp['pins'].items()):
            fail('local named integer pins required')
        if len({tuple(p) for p in fp['pins'].values()})!=len(fp['pins']): fail('duplicate local pin')
        fp['pins']={k:list(p) for k,p in sorted(fp['pins'].items())}
        fields(fp['physical'],FOOTPRINT_FIELDS,'footprint physical')
        for k,v in fp['physical'].items(): _query(v,'bool' if k=='pin_fit' else 'mm','footprints.'+name+'.'+k)
    for ref,part in parts.items():
        fields(part,('footprint','nets','rotations','fixed','region'),'component '+ref)
        if not text(part['footprint']) or part['footprint'] not in fps: fail('unknown footprint')
        fields(part['nets'],fps[part['footprint']]['pins'],'pin nets '+ref)
        if any(n is not None and not text(n) for n in part['nets'].values()): fail('net must be named or explicit null')
        rotations=part['rotations']
        if not isinstance(rotations,list) or not rotations or any(type(r) is not int or r not in (0,90,180,270) for r in rotations):
            fail('orthogonal rotations required')
        if len(set(rotations))!=len(rotations): fail('duplicate rotation')
        part['rotations']=sorted(rotations)
        if part['fixed'] is not None:
            fields(part['fixed'],('at','rotation'),'fixed')
            if not point(part['fixed']['at']) or type(part['fixed']['rotation']) is not int or part['fixed']['rotation'] not in rotations:
                fail('invalid fixed placement')
            part['fixed']['at']=list(part['fixed']['at'])
        r=part['region']
        if r is not None and (not rectangle(r) or not contains(grid,r[:2]) or not contains(grid,r[2:])): fail('invalid region')
    if sum(len(fps[p['footprint']]['pins']) for p in parts.values())>512: fail('input limit: 512 pins')
    if set(fps)!={p['footprint'] for p in parts.values()}: fail('unused or missing footprint')
    return json.loads(json.dumps(value,sort_keys=True,allow_nan=False))

def fixed_placements(value):
    value=validate(value)
    if any(p['fixed'] is None for p in value['components'].values()): fail('replay requires all positions fixed')
    return {r:deepcopy(p['fixed']) for r,p in value['components'].items()}

def pins(value, placements):
    value=validate(value)
    return _pins(value,placements)

def _pins(value, placements):
    if not isinstance(placements,dict) or not set(placements)<=set(value['components']): fail('unknown placement ref')
    out={}
    for ref,pos in sorted(placements.items()):
        fields(pos,('at','rotation'),'placement '+ref)
        if not point(pos['at']) or type(pos['rotation']) is not int or pos['rotation'] not in (0,90,180,270): fail('invalid placement')
        fp=value['footprints'][value['components'][ref]['footprint']]
        out[ref]={k:tuple(a+b for a,b in zip(pos['at'],rotate(p,pos['rotation']))) for k,p in fp['pins'].items()}
    return out

def pin_nets(value):
    value=validate(value)
    return {(r,k):n for r,p in value['components'].items() for k,n in p['nets'].items()}

def physical_inputs(value, facts):
    value=validate(value)
    if not isinstance(facts,FactManifest): fail('FactManifest required')
    values={}; issues=[]; resolutions={}
    specs={'board.'+k:v for k,v in value['physical'].items()}
    specs.update({'footprints.'+name+'.'+k:v for name,fp in value['footprints'].items() for k,v in fp['physical'].items()})
    for path,spec in sorted(specs.items()):
        if 'value' in spec:
            values[path]=None; resolutions[path]=spec
            issues.append(dict(path=path,code='MISSING',obtained_by=spec['obtained_by']))
            continue
        r=facts.resolve(spec['fact_id'],expected_unit=spec['unit'],allowed_provenance=spec['allowed_provenance'],
                        require_datum=True,expected_scope=spec['scope'])
        resolutions[path]=r.as_dict()
        code=str(r.status)
        if code=='READY' and not text(r.datum): code='AMBIGUOUS_DATUM'
        if code!='READY':
            values[path]=None;issues.append(dict(path=path,code=code,fact_id=spec['fact_id'],detail=r.detail))
            continue
        v=r.value; valid=False
        if path.endswith(('.pin_fit','.keepout_review')): valid=v is True
        elif path.endswith('.envelope_mm'):
            valid=isinstance(v,list) and len(v)==6 and all(number(n) for n in v) and all(v[i]<v[i+3] for i in range(3)) and v[2]>=0
        else:
            valid=number(v) and (v>=0 if path.endswith(('.edge_x_mm','.edge_y_mm','.clearance_mm')) else v>0)
        if not valid:
            values[path]=None;issues.append(dict(path=path,code='INVALID_VALUE',fact_id=spec['fact_id']))
        else: values[path]=v
    pitch=values.get('board.pitch_mm')
    if pitch is not None:
        for name in value['footprints']:
            k='footprints.'+name+'.pitch_mm'
            if values.get(k) is not None and not math.isclose(values[k],pitch,rel_tol=0,abs_tol=1e-9):
                issues.append(dict(path=k,code='PITCH_MISMATCH'))
    if pitch is not None:
        for count,edge,size in [('columns','edge_x_mm','width_mm'),('rows','edge_y_mm','height_mm')]:
            margin=values.get('board.'+edge);extent=values.get('board.'+size)
            if margin is not None and extent is not None and margin+(value['grid'][count]-1)*pitch>=extent:
                issues.append(dict(path='board.'+size,code='GRID_PHYSICAL_BOUNDS'))
        for name,fp in value['footprints'].items():
            box=values.get('footprints.'+name+'.envelope_mm')
            if box is not None and any(not (box[0]<=x*pitch<=box[3] and box[1]<=y*pitch<=box[4]) for x,y in fp['pins'].values()):
                issues.append(dict(path='footprints.'+name+'.envelope_mm',code='ENVELOPE_PINS'))
    status='INVALID' if any(i['code'] in ('INVALID_VALUE','PITCH_MISMATCH','GRID_PHYSICAL_BOUNDS','ENVELOPE_PINS') for i in issues) else ('INPUTS_INCOMPLETE' if issues else 'READY')
    return dict(status=status,values=values,resolutions=resolutions,issues=issues)

def placement_issues(value, placements, physical=None, *, partial=False):
    """Independent constraints; does not use the search's domain enumeration."""
    result=[]
    def issue(code,path,**details): result.append(dict(code=code,path=path,**details))
    table=_pins(value,placements)
    if not partial:
        for ref in sorted(set(value['components'])-set(placements)): issue('MISSING_PLACEMENT',ref)
    taken={}; boxes={}
    grid=[0,0,value['grid']['columns']-1,value['grid']['rows']-1]
    for ref,part_pins in table.items():
        part=value['components'][ref];pos=placements[ref]
        if pos['rotation'] not in part['rotations']: issue('ROTATION',ref)
        if part['fixed'] is not None and pos!=part['fixed']: issue('FIXED',ref)
        for key,at in part_pins.items():
            path=ref+'.'+key
            if not contains(grid,at): issue('GRID_BOUNDS',path)
            if part['region'] is not None and not contains(part['region'],at): issue('REGION',path)
            if any(contains(r,at) for r in value['keepouts']): issue('KEEPOUT',path)
            if at in taken: issue('PAD_COLLISION',path,other=taken[at])
            taken[at]=path
        if physical is None: continue
        p=physical; pitch=p['board.pitch_mm']
        e=p['footprints.'+part['footprint']+'.envelope_mm']
        corners=[rotate((x,y),pos['rotation']) for x in (e[0],e[3]) for y in (e[1],e[4])]
        xs=[c[0]+pos['at'][0]*pitch for c in corners];ys=[c[1]+pos['at'][1]*pitch for c in corners]
        box=(min(xs),min(ys),e[2],max(xs),max(ys),e[5]);boxes[ref]=box
        xmin=-p['board.edge_x_mm'];ymin=-p['board.edge_y_mm']
        if box[0]<xmin or box[1]<ymin or box[3]>xmin+p['board.width_mm'] or box[4]>ymin+p['board.height_mm']: issue('BODY_BOUNDS',ref)
        if box[5]>p['board.max_height_mm']: issue('HEIGHT',ref)
        for r in value['keepouts']:
            # A forbidden hole reserves its entire grid cell through all heights.
            if box[0]<(r[2]+.5)*pitch and box[3]>(r[0]-.5)*pitch and box[1]<(r[3]+.5)*pitch and box[4]>(r[1]-.5)*pitch:
                issue('BODY_KEEPOUT',ref)
    if physical is not None:
        gap=physical['board.clearance_mm']
        for i,(ref,a) in enumerate(sorted(boxes.items())):
            for other,b in sorted(boxes.items())[i+1:]:
                if all(a[k]<b[k+3]+gap and b[k]<a[k+3]+gap for k in range(3)): issue('BODY_COLLISION',ref,other=other)
    return result

def audit(value, placements, facts=None):
    value=validate(value)
    physical=physical_inputs(value,facts) if facts is not None else None
    issues=placement_issues(value,placements,physical['values'] if physical and physical['status']=='READY' else None)
    if physical: issues+=physical['issues']
    status='INVALID' if any(i['code'] not in ('MISSING','WRONG_UNIT','WRONG_SCOPE','INSUFFICIENT_PROVENANCE','AMBIGUOUS_DATUM') for i in issues) else (
        'INPUTS_INCOMPLETE' if physical and physical['status']!='READY' else ('VALID' if physical else 'LOGICAL_VALID'))
    return dict(status=status,issues=issues,physical_release=False)

def input_hash(value, physical):
    payload=dict(model=validate(value),facts=physical['resolutions'])
    # Invalid non-finite values must still produce a serializable diagnostic.
    def safe(v):
        if isinstance(v,float) and not math.isfinite(v): return {'invalid_number':str(v)}
        if isinstance(v,dict): return {k:safe(x) for k,x in v.items()}
        if isinstance(v,list): return [safe(x) for x in v]
        return v
    return sha256(json.dumps(safe(payload),sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()).hexdigest()
