"""Portable, adapter-authored solder operations and readable paper fragments.

A plan specifies a viewing sequence, never records physical completion. Hole
coordinates and connectivity come from the existing board contract.
"""
from __future__ import annotations
from copy import deepcopy
from html import escape
import math
import re
from xml.etree import ElementTree as ET

SCHEMA = 'perfboard-solder-plan-v1'


def validate(layout, plan):
    def fail(message): raise ValueError('BOARD_PLAN: '+message)
    def text(x): return isinstance(x,str) and bool(x.strip())
    if not isinstance(plan,dict) or set(plan)!={'schema','source','steps','operations'}:
        fail('expected schema, source, steps and operations')
    if plan['schema']!=SCHEMA or not text(plan['source']): fail('invalid schema or source')
    steps=plan['steps']; ops=plan['operations']
    if not isinstance(steps,list) or not steps or not all(map(text,steps)): fail('invalid source steps')
    if not isinstance(ops,list) or not ops: fail('missing operations')
    refs={p['ref'] for p in layout['pins']}; links={l['id'] for l in layout['links']}
    fields={'id','kind','source_step','title','action','side','refs','links','material','check'}
    seen=set(); covered=[]; order=[]
    for op in ops:
        if not isinstance(op,dict) or not fields<=set(op)<=fields|{'pins'}: fail('invalid operation fields')
        if not all(text(op[k]) for k in ('id','title','action','material','check')): fail('empty operation text')
        if op['id'] in seen: fail('duplicate operation id')
        seen.add(op['id'])
        if op['kind'] not in ('prepare','place','connect','inspect'): fail('unknown operation kind')
        if op['side'] not in ('front','back'): fail('unknown board side')
        step=op['source_step']
        if type(step) is not int or not 1<=step<=len(steps): fail('invalid source step')
        order.append(step)
        for name,known in (('refs',refs),('links',links)):
            values=op[name]
            if not isinstance(values,list) or not all(isinstance(v,str) for v in values):
                fail('invalid '+name)
            if len(set(values))!=len(values) or not set(values)<=known: fail('unknown or duplicate '+name)
        if 'pins' in op:
            known={p['ref']+'.'+p['pin'] for p in layout['pins'] if p['ref'] in op['refs']}
            chosen=op['pins']
            if (op['kind']!='place' or not isinstance(chosen,list) or not chosen or
                not all(isinstance(p,str) for p in chosen) or len(set(chosen))!=len(chosen) or
                not set(chosen)<=known): fail('unknown or invalid placement pins')
        if op['kind']=='connect':
            if len(op['links'])!=1: fail('one conductor per connect operation required')
            covered.extend(op['links'])
        elif op['links']: fail('only connect operations may own conductors')
        if op['kind']=='place' and not op['refs']: fail('placement needs component references')
    if order!=sorted(order) or set(order)!=set(range(1,len(steps)+1)):
        fail('source steps must be covered in their declared order')
    if len(covered)!=len(set(covered)) or set(covered)!=links: fail('every conductor must appear exactly once')
    return plan


def targets(layout, operation):
    """Resolve exact holes; a jumper crossing does not imply a solder joint."""
    selected=[l for l in layout['links'] if l['id'] in operation['links']]
    points={tuple(p['at']) for p in layout['pins'] if p['ref'] in operation['refs'] and
            ('pins' not in operation or p['ref']+'.'+p['pin'] in operation['pins'])}
    for link in selected:
        a,b=link['from'],link['to']
        points.update((tuple(a),tuple(b)))
        if link['kind']!='jumper':
            for hole in layout['holes']:
                x,y=hole['at']
                if (hole['net']==link['net'] and
                    (x-a[0])*(b[1]-a[1])==(y-a[1])*(b[0]-a[0]) and
                    min(a[0],b[0])<=x<=max(a[0],b[0]) and min(a[1],b[1])<=y<=max(a[1],b[1])):
                    points.add((x,y))
    pads=[h for h in layout['holes'] if tuple(h['at']) in points]
    nets=list(dict.fromkeys(h['net'] for h in pads if h['net']))
    return {'nets':nets,'links':[l['id'] for l in selected],
            'pads':[','.join(map(str,h['at'])) for h in pads]}


def _windows(size, count, overlap=2):
    start=0
    while True:
        end=min(size-1,start+count-1)
        yield start,end
        if end==size-1: break
        start=end-overlap+1


def grid_regions(columns, rows):
    """Inclusive hole bounds, with two shared rows/columns between sheets."""
    return [(x0,y0,x1,y1) for y0,y1 in _windows(rows,10)
            for x0,x1 in _windows(columns,13)]


def reference_tiles(source, *, prefix):
    """Crop the supplied SVG, retaining all its definitions and orientation text.

    At 190 mm (718 CSS pixels), the smallest explicit source font is at least
    12 CSS pixels. Fragments overlap; this is magnification, not new geometry.
    """
    ET.register_namespace('','http://www.w3.org/2000/svg')
    root=ET.fromstring(source)
    box=[float(x) for x in root.get('viewBox','').replace(',',' ').split()]
    if len(box)!=4: raise ValueError('BOARD_PRINT: source SVG needs a viewBox')
    x,y,w,h=box
    fonts=[float(v) for v in re.findall(r'font-size[=:]\s*["\x27]?\s*([0-9.]+)',source)]
    # CSS shorthand is accepted too; the explicit source sizes take precedence.
    if not fonts: fonts=[float(v) for v in re.findall(r'font\s*:\s*(?:\w+\s+)*([0-9.]+)px',source)]
    minfont=min(fonts) if fonts else 10
    limit=min(600,718*minfont/12)
    overlap=24
    nx=max(1,math.ceil((w-overlap)/(limit-overlap)))
    tilew=(w+(nx-1)*overlap)/nx
    ny=max(1,math.ceil((h-overlap)/(tilew*1.2-overlap)))
    tileh=(h+(ny-1)*overlap)/ny
    tiles=[]
    for row in range(ny):
        for col in range(nx):
            clone=deepcopy(root)
            clone.set('data-fragment',f'ряд {row+1}/{ny}, столбец {col+1}/{nx}')
            clone.set('viewBox',f'{x+col*(tilew-overlap):g} {y+row*(tileh-overlap):g} {tilew:g} {tileh:g}')
            clone.set('width',f'{tilew:g}');clone.set('height',f'{tileh:g}')
            old_ids={n.get('id'):f'{prefix}-{row}-{col}-{n.get("id")}' for n in clone.iter() if n.get('id')}
            for node in clone.iter():
                for attr,value in list(node.attrib.items()):
                    if attr=='id': value=old_ids[value]
                    else:
                        for before,after in old_ids.items():
                            value=value.replace('url(#'+before+')','url(#'+after+')')
                            if value=='#'+before: value='#'+after
                    node.set(attr,value)
                if node.tag.rsplit('}',1)[-1]=='style' and node.text:
                    for before,after in old_ids.items():
                        node.text=re.sub(r'#'+re.escape(before)+r'(?![\w-])','#'+after,node.text)
            tiles.append(ET.tostring(clone,encoding='unicode'))
    return tiles


def endpoint(layout, point):
    names=[p['ref']+'.'+p['pin'] for p in layout['pins'] if p['at']==point]
    return '('+','.join(map(str,point))+')'+(' · '+' / '.join(names) if names else ' · площадка')


def operation_card(plan, key):
    esc=lambda s:escape(str(s),quote=True)
    options='<option value="">Просмотр схемы · выберите операцию</option>'+''.join(
        f'<option value="{i}">{i+1}. {esc(op["title"])}</option>' for i,op in enumerate(plan['operations']))
    return (f'<div class="board-operation"><h3 id="{key}-solder">Порядок пайки</h3>'
        '<label>Операция <select class="board-operation-select">'+options+'</select></label>'
        '<div class="board-tools"><button type="button" data-board-action="operation-previous">← Назад</button>'
        '<button type="button" data-board-action="operation-next">Далее →</button></div>'
        '<p class="board-operation-mode" role="status">Выберите первую операцию для начала просмотра.</p>'
        '<button type="button" data-board-action="operation-resume" hidden>Вернуться к операции</button>'
        '<div class="board-operation-content" hidden><p class="board-operation-action"></p>'
        '<p><b>Контакты:</b> <span class="board-operation-endpoints"></span></p>'
        '<p><b>Материал:</b> <span class="board-operation-material"></span></p>'
        '<p><b>Проверка:</b> <span class="board-operation-check"></span></p>'
        '<details><summary>Исходный шаг</summary><p class="board-operation-source"></p></details></div>'
        '<p class="note">Это просмотр последовательности. Выполнение и физический допуск здесь не отмечаются.</p></div>')
