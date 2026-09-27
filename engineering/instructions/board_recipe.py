"""Compile explicit operation dependencies and named-pin text from one board result."""
from collections import Counter
from copy import deepcopy
import re
from engineering import board_model as model, board_pipeline as pipeline
from engineering.instructions import board_solder, board_workspace

SCHEMA = 'perfboard-recipe-v1'
TOKEN = re.compile(r'\{\{([^{}]+)\}\}')
COORDINATE = re.compile(r'\(\s*-?\d+\s*,\s*-?\d+\s*\)')

def text(template, record, link=None):
    if not model.text(template): model.fail('empty recipe text')
    if COORDINATE.search(template): model.fail('literal coordinates in recipe; use named pins')
    layout=record['layout']; value=record['model']
    positions=(record['calculation']['best']['placements'] if record['mode']=='search'
               else model.fixed_placements(value))
    pins={p['ref']+'.'+p['pin']:p['at'] for p in layout['pins']}
    def resolve(match):
        key=match.group(1)
        if key in ('from','to','net'):
            if link is None: model.fail('link token outside connection')
            return link['net'] if key=='net' else board_solder.endpoint(layout,link[key])
        kind,_,name=key.partition(':')
        if kind=='pin' and name in pins: return board_solder.endpoint(layout,pins[name])
        if kind=='contacts' and name in positions:
            return '; '.join(board_solder.endpoint(layout,p['at']) for p in layout['pins'] if p['ref']==name)
        if kind=='direction':
            ref,_,direction=name.rpartition(':')
            vectors={'right':(1,0),'down':(0,1),'left':(-1,0),'up':(0,-1)}
            words={(1,0):'вправо',(0,1):'вниз',(-1,0):'влево',(0,-1):'вверх'}
            if ref in positions and direction in vectors:
                return words[model.rotate(vectors[direction],positions[ref]['rotation'])]
        model.fail('unknown recipe token '+key)
    out=TOKEN.sub(resolve,template)
    if '{{' in out or '}}' in out: model.fail('malformed recipe token')
    return out

def compile(record, recipe):
    pipeline.inspect(record)
    if record['layout'] is None: model.fail('board has no usable placement')
    model.fields(recipe,('schema','revision','source','groups'),'recipe')
    if recipe['schema']!=SCHEMA or not model.text(recipe['revision']) or not model.text(recipe['source']):
        model.fail('recipe schema/revision/source')
    groups=recipe['groups']
    if not isinstance(groups,dict) or not groups or len(groups)>512 or not all(model.text(k) for k in groups):
        model.fail('invalid recipe groups')
    for key,group in groups.items():
        model.fields(group,('requires','title','operations'),'recipe group '+key)
        req=group['requires']
        if (not isinstance(req,list) or not all(isinstance(v,str) for v in req) or
            len(req)!=len(set(req)) or not set(req)<=set(groups)):
            model.fail('unknown/duplicate recipe dependency')
        if not isinstance(group['operations'],list) or not group['operations']:
            model.fail('empty operation group')
    ordered=[]; remaining=set(groups)
    while remaining:
        ready=sorted(k for k in remaining if set(groups[k]['requires'])<=set(ordered))
        if not ready: model.fail('recipe dependency cycle')
        ordered.extend(ready); remaining.difference_update(ready)
    layout=record['layout']; steps=[]; operations=[]; placement_pins=[]
    for group_id in ordered:
        group=groups[group_id]; step=len(steps)+1
        steps.append(text(group['title'],record))
        for index,rule in enumerate(group['operations']):
            model.fields(rule,('kind','title','action','material','check','refs','pins','select'),'operation rule')
            if rule['kind'] not in ('prepare','place','connect','inspect'): model.fail('recipe operation kind')
            refs=rule['refs']; pins=rule['pins']
            if not isinstance(refs,list) or not all(isinstance(r,str) for r in refs) or len(refs)!=len(set(refs)):
                model.fail('recipe references')
            known={p['ref'] for p in layout['pins']}
            if not set(refs)<=known: model.fail('unknown recipe component')
            if pins is not None and (not isinstance(pins,list) or not pins or not all(isinstance(p,str) for p in pins)):
                model.fail('recipe pin selection')
            selected=[None]
            if rule['kind']=='connect':
                if refs or pins is not None: model.fail('connection targets are selected conductors')
                select=rule['select']; model.fields(select,('kinds','priority'),'conductor selector')
                if (not isinstance(select['kinds'],list) or not select['kinds'] or
                    any(k not in board_workspace.KINDS for k in select['kinds']) or
                    len(set(select['kinds']))!=len(select['kinds'])): model.fail('conductor kinds')
                priority=select['priority']
                if not isinstance(priority,list) or not all(model.text(n) for n in priority) or len(set(priority))!=len(priority):
                    model.fail('conductor priority')
                selected=[l for l in layout['links'] if l['kind'] in select['kinds']]
                selected.sort(key=lambda l:(priority.index(l['net']) if l['net'] in priority else len(priority)))
            elif rule['select'] is not None: model.fail('only connections select links')
            if rule['kind']=='place':
                selected_pins=[p['ref']+'.'+p['pin'] for p in layout['pins'] if p['ref'] in refs]
                placement_pins.extend(selected_pins if pins is None else pins)
            for link in selected:
                op=dict(id=group_id+'-'+str(index)+(('-'+link['id']) if link else ''),kind=rule['kind'],
                    source_step=step,side='back' if link else 'front',refs=refs,
                    links=[link['id']] if link else [],
                    **{k:text(rule[k],record,link) for k in ('title','action','material','check')})
                if pins is not None: op['pins']=pins
                operations.append(op)
    counts=Counter(placement_pins)
    expected={p['ref']+'.'+p['pin'] for p in layout['pins']}
    if set(counts)!=expected or any(v!=1 for v in counts.values()): model.fail('every component pin must be placed exactly once')
    return board_solder.validate(layout,dict(schema=board_solder.SCHEMA,source=recipe['source'],
                                              steps=steps,operations=operations))

def views(record, recipe, *, title, checks=()):
    """Every generated coordinate-bearing field originates in the same layout."""
    plan=compile(record,recipe); layout=record['layout']
    reference=deepcopy(layout);reference['links']=[]
    neighbours=[]
    for i,a in enumerate(layout['holes']):
        for b in layout['holes'][i+1:]:
            if a['net'] and b['net'] and a['net']!=b['net'] and sum(abs(x-y) for x,y in zip(a['at'],b['at']))==1:
                neighbours.append((a['at'],b['at'],a['net'],b['net'],'При замыкании соединятся разные цепи; проверить отсутствие припоя между площадками.'))
    return dict(layout=layout,plan=plan,steps=plan['steps'],bom=deepcopy(record['bom']),
        svg=board_workspace.svg(reference,side='front',title=title),
        svg_stage1='',grid='',neighbours=neighbours,
        solder_svg=board_workspace.svg(layout,side='back',title=title),
        solder_table=board_workspace.connection_table(layout,interactive=False),
        checks=[[text(cell,record) for cell in row] for row in checks],
        chain=record,recipe=recipe,check_templates=deepcopy(list(checks)),automation={'value':record['model'],'result':record['calculation']})


def validate_view(board):
    expected=views(board['chain'],board['recipe'],title=board['title'],checks=board['check_templates'])
    for field in expected:
        if board.get(field)!=expected[field]:
            model.fail('CHAIN_VIEW: '+field+' differs from the selected board record')
