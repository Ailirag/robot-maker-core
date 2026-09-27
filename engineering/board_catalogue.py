"""Compile versioned component catalogues and board instances into the grid model."""
from copy import deepcopy
from hashlib import sha256
import json
from engineering import board_model as model

SCHEMA = 'perfboard-catalogue-v1'
DESIGN_SCHEMA = 'perfboard-design-v1'

def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def compile(catalogue, design):
    model.fields(catalogue, ('schema','revision','footprints','parts'), 'catalogue')
    model.fields(design, ('schema','revision','grid','physical','keepouts','components'), 'design')
    if catalogue['schema'] != SCHEMA or design['schema'] != DESIGN_SCHEMA:
        model.fail('catalogue/design schema')
    if not model.text(catalogue['revision']) or not model.text(design['revision']):
        model.fail('explicit revision required')
    fps, types, instances = catalogue['footprints'], catalogue['parts'], design['components']
    for label, value in [('footprints',fps),('parts',types),('components',instances)]:
        if not isinstance(value,dict) or not value or not all(model.text(k) for k in value):
            model.fail('catalogue '+label)
    for key, fp in fps.items():
        model.fields(fp, ('revision','obtained_by','pins','physical'), 'catalogue footprint '+key)
        if not model.text(fp['revision']) or not model.text(fp['obtained_by']):
            model.fail('footprint revision and origin required')
    for key, part in types.items():
        model.fields(part, ('revision','label','footprint')+(('provision',) if 'provision' in part else ()), 'catalogue part '+key)
        if 'provision' in part:
            model.fields(part['provision'],('status','source'),'provision')
            if not all(model.text(v) for v in part['provision'].values()): model.fail('explicit provision required')
        if not all(model.text(part[k]) for k in ('revision','label','footprint')) or part['footprint'] not in fps:
            model.fail('invalid catalogue part '+key)
    components, used, bom = {}, {}, []
    for ref, part in sorted(instances.items()):
        model.fields(part, ('part','nets','rotations','fixed','region'), 'instance '+ref)
        if not model.text(part['part']) or part['part'] not in types:
            model.fail('unknown catalogue part '+ref)
        item=types[part['part']]; fp=item['footprint']
        used[fp]={k:deepcopy(fps[fp][k]) for k in ('pins','physical')}
        components[ref]={k:deepcopy(v) for k,v in part.items() if k!='part'}
        components[ref]['footprint']=fp
        provision=item.get('provision',{'status':'Состав проекта; установка не подтверждена','source':'Каталог'})
        bom.append([ref,item['label'],provision['status'],
                    provision['source']+' / '+part['part']+' @ '+item['revision']+' / '+fp+' @ '+fps[fp]['revision']])
    value=model.validate(dict(schema=model.SCHEMA, **{k:deepcopy(design[k]) for k in ('grid','physical','keepouts')},
                              footprints=used,components=components))
    return dict(model=value,bom=bom,catalogue_sha256=digest(catalogue),design_sha256=digest(design))
