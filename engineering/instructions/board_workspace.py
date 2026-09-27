"""Portable perfboard workbench. Coordinates are holes, never inferred dimensions.

The adapter supplies existing topology and source procedures. Selecting a conductor
is inspection, not an assembly sequence or a physical acceptance record.
"""
from __future__ import annotations
from hashlib import sha256
from html import escape
import json
import re
from xml.etree import ElementTree as ET
from . import board_solder

SCHEMA = 'perfboard-workspace-v1'
KINDS = {'wire':'Лужёный провод', 'bridge':'Припой', 'jumper':'Изолированная перемычка'}

def _text(value):
    return isinstance(value,str) and bool(value.strip())

def _id(net, a, b, kind):
    return 'c-'+sha256(json.dumps([net,sorted([a,b]),kind],ensure_ascii=False).encode()).hexdigest()[:16]

def from_topology(*, columns, rows, holes, links, pins):
    """Normalize a project's existing board maps into the portable contract."""
    value={'schema':SCHEMA,'columns':columns,'rows':rows,
           'holes':[{'at':list(at),'net':net} for at,net in holes.items()],
           'pins':[{'ref':ref,'pin':str(pin),'at':list(at),'net':holes.get(tuple(at))}
                   for ref,parts in pins.items() for pin,at in parts.items()],
           'links':[{'id':_id(net,list(a),list(b),kind),'net':net,
                     'from':list(a),'to':list(b),'kind':kind} for net,a,b,kind in links]}
    return validate(value)

def validate(value):
    def fail(message): raise ValueError('BOARD_CONTRACT: '+message)
    if not isinstance(value,dict) or set(value)!={'schema','columns','rows','holes','pins','links'}:
        fail('expected schema, grid, holes, pins and links')
    if value['schema']!=SCHEMA: fail('unsupported schema')
    for key in ('columns','rows'):
        if type(value[key]) is not int or not 1<=value[key]<=200: fail('invalid '+key)
    def at(point):
        if not isinstance(point,list) or len(point)!=2 or any(type(x) is not int for x in point):
            fail('hole must be two integers')
        if not (0<=point[0]<value['columns'] and 0<=point[1]<value['rows']): fail('hole outside grid')
        return tuple(point)
    def rows(key, fields):
        if not isinstance(value[key],list): fail(key+' must be a list')
        for row in value[key]:
            if not isinstance(row,dict) or set(row)!=fields: fail('invalid '+key+' row')
            yield row
    holes={}
    for h in rows('holes',{'at','net'}):
        p=at(h['at'])
        if p in holes: fail('duplicate hole')
        if h['net'] is not None and not _text(h['net']): fail('invalid net')
        holes[p]=h['net']
    pins=set()
    for p in rows('pins',{'ref','pin','at','net'}):
        coord=at(p['at'])
        if not _text(p['ref']) or not _text(p['pin']): fail('missing pin identity')
        identity=(p['ref'],p['pin'])
        if identity in pins: fail('duplicate pin identity')
        pins.add(identity)
        if coord not in holes or p['net']!=holes[coord]: fail('pin does not match pad net')
    identities=set()
    for link in rows('links',{'id','net','from','to','kind'}):
        a,b=at(link['from']),at(link['to'])
        if not _text(link['net']) or link['kind'] not in KINDS: fail('unknown net or conductor type')
        if a==b or any(p not in holes or holes[p]!=link['net'] for p in (a,b)):
            fail('conductor endpoints do not match net')
        key=_id(link['net'],list(a),list(b),link['kind'])
        if link['id']!=key or key in identities: fail('duplicate or unstable conductor identity')
        identities.add(key)
    return value

def anchor(ref, stage):
    def slug(text):
        readable=re.sub(r'[^a-z0-9_-]+','-',str(text).lower()).strip('-')
        return readable if readable==str(text).lower() and readable else 'id-'+sha256(str(text).encode()).hexdigest()[:12]
    return 'board-'+slug(stage or 'default')+'-'+slug(ref)

def _esc(value): return escape(str(value),quote=True)
def _coord(point): return ','.join(map(str,point))

def svg(layout, *, side, title, region=None, prefix=''):
    validate(layout)
    if side not in ('front','back'): raise ValueError('BOARD_SIDE: front or back required')
    cols,rows=layout['columns'],layout['rows']
    x0,y0,x1,y1=region or (0,0,cols-1,rows-1)
    pitch=32; width=112+(x1-x0)*pitch; height=130+(y1-y0)*pitch
    def xy(point):
        c,r=point
        return (56+(x1-c if side=='back' else c-x0)*pitch,70+(r-y0)*pitch)
    def inside(point): return x0<=point[0]<=x1 and y0<=point[1]<=y1
    label='Сторона деталей' if side=='front' else 'Сторона пайки'
    items=[f'<svg xmlns="http://www.w3.org/2000/svg" class="board-grid" viewBox="0 0 {width} {height}" width="{width}" height="{height}" data-side="{side}" role="group" aria-label="{_esc(title)} — {label}">',
           f'<title>{_esc(title)} — {label}</title><text x="{width/2}" y="22" text-anchor="middle" class="board-orientation">ВЕРХ ПЛАТЫ · {label}</text>']
    for c in range(x0,x1+1):
        x,_=xy((c,y0)); items.append(f'<text x="{x}" y="48" text-anchor="middle" class="board-coordinate">{c}</text>')
    for r in range(y0,y1+1):
        _,y=xy((x0,r)); items.append(f'<text x="20" y="{y+4}" class="board-coordinate">{r}</text>')
    for r in range(y0,y1+1):
        for c in range(x0,x1+1):
            x,y=xy((c,r)); items.append(f'<circle cx="{x}" cy="{y}" r="2.5" fill="#cbd5df"/>')
    if region:
        clip='clip-'+sha256((prefix+side+str(region)).encode()).hexdigest()[:16]
        items.append(f'<defs><clipPath id="{clip}"><rect x="46" y="60" width="{width-92}" height="{height-110}"/></clipPath></defs><g clip-path="url(#{clip})">')
    for link in layout['links']:
        xa,ya=xy(link['from']);xb,yb=xy(link['to'])
        attrs=f'data-link="{link["id"]}" data-net="{_esc(link["net"])}" data-kind="{link["kind"]}"'
        text=f'{link["net"]}: ({_coord(link["from"])}) → ({_coord(link["to"])}), {KINDS[link["kind"]]}'
        items.append(f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" {attrs} class="board-conductor"><title>{_esc(text)}</title></line>')
    if region: items.append('</g>')
    pins={}
    for p in layout['pins']: pins.setdefault(tuple(p['at']),[]).append(p['ref']+'.'+p['pin'])
    for h in layout['holes']:
        if not inside(h['at']): continue
        x,y=xy(h['at']); key=_coord(h['at']); net=h['net'] or ''
        identity=' / '.join(pins.get(tuple(h['at']),[]))
        text=f'({key}) · {identity or "площадка"} · {net or "нет подключения"}'
        items.append(f'<circle cx="{x}" cy="{y}" r="5" class="board-pad" data-hole="{key}" data-net="{_esc(net)}" tabindex="0" role="button" aria-label="{_esc(text)}"><title>{_esc(text)}</title></circle>')
        if identity:
            items.append(f'<text x="{x+8}" y="{y-9}" class="board-pin-label" data-label-pad="{key}">{_esc(identity)}</text>')
    for ref in dict.fromkeys(p['ref'] for p in layout['pins']):
        group=[p for p in layout['pins'] if p['ref']==ref and inside(p['at'])]
        if not group: continue
        x,y=xy(group[0]['at'])
        align='end' if x>width-90 else 'start'
        dx=-8 if align=='end' else 8
        items.append(f'<text x="{x+dx}" y="{y+17}" text-anchor="{align}" class="board-component-label" data-component="{_esc(ref)}">{_esc(ref)}</text>')
    if inside((0,0)):
        x,y=xy((0,0))
        items.append(f'<path data-origin="0,0" data-x="{x}" class="board-origin" d="M {x-7} {y-10} L {x+7} {y-10} L {x} {y-20} Z"><title>Один физический угол: (0,0)</title></path>')
    items.append(f'<text x="{width/2}" y="{height-25}" text-anchor="middle" class="board-coordinate">▲ (0,0) · {"справа" if side=="back" else "слева"} сверху на целой плате</text>')
    items.append(f'<text x="{width/2}" y="{height-8}" text-anchor="middle" class="board-coordinate">Координаты стороны деталей · столбец, строка</text></svg>')
    return ''.join(items)

def _table(headers, rows, cls=''):
    return '<table class="'+cls+'"><thead><tr>'+''.join('<th>'+_esc(x)+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+str(x)+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'

def connection_table(layout, *, interactive):
    pins={}
    for p in layout['pins']: pins.setdefault(tuple(p['at']),[]).append(p['ref']+'.'+p['pin'])
    def end(point):
        name=' / '.join(pins.get(tuple(point),[]))
        return _esc('('+_coord(point)+')'+(' '+name if name else ''))
    rows=[]
    for n,link in enumerate(layout['links'],1):
        name=_esc(link['net'])
        if interactive: name=f'<button type="button" data-select-link="{link["id"]}">{name}</button>'
        cells=[str(n),name,end(link['from']),end(link['to']),_esc(KINDS[link['kind']])]
        rows.append('<tr data-row-link="'+link['id']+'">'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
    return '<table class="board-connections"><thead><tr>'+''.join('<th>'+h+'</th>' for h in ('№','Цепь','Откуда','Куда','Проводник'))+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table>'

def render(board, *, stage='', stage_title=''):
    if board.get('chain'):
        from . import board_recipe
        board_recipe.validate_view(board)
    layout=validate(board['layout']); key=anchor(board['ref'],stage)
    plan=board_solder.validate(layout,board['plan']) if board.get('plan') else None
    if plan and plan['steps']!=board.get('steps',[]): raise ValueError('BOARD_PLAN: displayed source steps differ')
    def payload(cls,value):
        data=json.dumps(value,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
        return '<script type="application/json" class="'+cls+'">'+data+'</script>'
    title=board['title']; parts=[]
    def heading(suffix, text):
        return '<h3 id="'+key+'-'+suffix+'">'+text+'</h3>'
    parts.append(f'<section class="board-sheet solder-sheet" data-board="{key}"><h2 id="{key}" data-nav-label="{_esc(board["ref"])} · плата">{_esc(title)}</h2>')
    parts.append('<p class="board-stage"><b>Ступень и состав:</b> '+_esc(stage_title)+'</p>')
    for field,label in (('intro','Назначение'),('where','Где в корпусе'),('substrate','Основа')):
        if board.get(field): parts.append('<p><b>'+label+':</b> '+_esc(board[field])+'</p>')
    if board.get('stage_note'): parts.append('<div class="warning">'+_esc(board['stage_note'])+'</div>')
    parts.append('<div class="stop">Питание отключено. Эта инструкция не снимает PRINT / POWERED HOLD. Допуск и приёмка — по плану измерений.</div>')
    if board.get('automation'):
        from . import board_automation
        parts.append(heading('calculation','Расчёт раскладки'))
        parts.append(board_automation.render(**board['automation'],filename_prefix=key))
    if board.get('chain'):
        import base64
        from engineering.board_catalogue import digest
        publication={'schema':'perfboard-publication-v1','chain':board['chain'],
                     'recipe':board['recipe'],'checks':board['check_templates']}
        publication['sha256']=digest(publication)
        encoded=base64.b64encode(json.dumps(publication,ensure_ascii=False,sort_keys=True).encode()).decode()
        parts.append(payload('board-chain',publication))
        parts.append('<p class="board-chain-download"><a download="'+key+'-publication.json" href="data:application/json;base64,'+encoded+'">Скачать каталог, модель, результат и правила монтажа</a></p>')

    parts.append(heading('orientation','Ориентация платы'))
    parts.append('<p>Отметьте один физический угол (0,0): на стороне деталей он слева сверху и обозначен ▲. Верх платы остаётся сверху. Для стороны пайки поверните плату вокруг вертикальной оси: тот же угол и столбец 0 окажутся справа. Координаты всегда относятся к стороне деталей.</p>')
    if board.get('svg'):
        parts.append('<details class="board-reference" open><summary>Расположение деталей и полярность · исходный вид</summary><p>Обозначения этого исходного рисунка читайте по его собственной легенде. Рабочие виды ниже используют единую легенду проводников.</p><div class="board-reference-scroll">'+board['svg']+'</div></details>')
    parts.append(heading('components','Детали платы'))
    parts.append(_table(('Поз.','Что','Состояние','Откуда'),([_esc(x) for x in row] for row in board.get('bom',[]))))
    parts.append(heading('workbench','Схема и выбор цепи'))
    data=json.dumps(layout,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    parts.append('<script type="application/json" class="board-data">'+data+'</script>')
    if plan:
        parts.append(payload('board-plan',plan))
        parts.append(payload('board-targets',{op['id']:board_solder.targets(layout,op) for op in plan['operations']}))
    parts.append('<div class="board-workbench"><div class="board-tools"><label>Найти цепь, деталь или контакт <input class="board-search" type="search" placeholder="Имя цепи, детали или координаты" autocomplete="off"></label><button type="button" data-board-action="clear">Все соединения</button><button type="button" data-board-action="large" aria-expanded="false">Крупный вид</button><button type="button" data-board-action="close-large" hidden>Закрыть крупный вид</button></div><div class="board-search-results" aria-label="Результаты поиска"></div>')
    parts.append('<div class="board-operation-workspace">')
    if plan: parts.append(board_solder.operation_card(plan,key))
    else: parts.append('<p class="board-plan-missing">План операций пайки не задан. Доступен просмотр топологии; порядок соединений из таблицы не выводится.</p>')
    parts.append('<div class="board-diagram-panel">')
    parts.append('<div class="board-tools"><button type="button" data-side-choice="front" aria-pressed="true">Сторона деталей</button><button type="button" data-side-choice="back" aria-pressed="false">Сторона пайки</button><button type="button" data-board-action="zoom-out" aria-label="Уменьшить схему">−</button><output class="board-zoom">100%</output><button type="button" data-board-action="zoom-in" aria-label="Увеличить схему">+</button></div>')
    parts.append('<p class="board-selection" role="status" aria-live="polite">Показаны все соединения. Выберите контакт на схеме или найдите цепь по имени.</p>')
    parts.append('<div class="board-viewport" tabindex="0" role="region" aria-label="Схема платы; прокрутка по горизонтали">')
    parts.append('<div class="board-view" data-board-side="front">'+svg(layout,side='front',title=title)+'</div>')
    parts.append('<div class="board-view" data-board-side="back" hidden>'+svg(layout,side='back',title=title)+'</div></div>')
    parts.append('<p class="board-legend"><span class="legend-wire">━</span> лужёный провод · <span class="legend-bridge">─</span> припой · <span class="legend-jumper">┄</span> изолированная перемычка. Выбранная цепь выделена толщиной и подписью. Нажатие площадки показывает контакты и цепь.</p><p class="note">Это топология соединений по отверстиям. Линия перемычки связывает её концы и не задаёт длину или физическую трассу провода. Постоянные подписи обозначают группы выводов, а не габариты корпусов. Размещение и полярность деталей сверяйте с исходным видом.</p></div></div>')
    parts.append('<div class="board-tools"><button type="button" data-board-action="previous">Предыдущее соединение</button><button type="button" data-board-action="next">Следующее соединение</button><span>Просмотр строк таблицы, не порядок пайки.</span></div>')
    parts.append(heading('connections','Соединения платы')+connection_table(layout,interactive=True)+'</div>')
    parts.append('<div class="board-print"><h4>Сторона пайки · полный состав соединений</h4>'+svg(layout,side='back',title=title)+'<p>Толстая линия — лужёный провод; тонкая — припой; штриховая — изолированная перемычка. Связи и контакты задаёт полная таблица.</p>'+connection_table(layout,interactive=False)+'</div>')
    if board.get('svg'):
        parts.append('<div class="board-print board-print-tiles"><h4>Крупные фрагменты расположения деталей</h4><p>Это увеличенные части исходного рисунка, с перекрытием соседних листов. Сторона деталей; общий угол (0,0) — слева сверху на целой плате.</p>')
        for i,tile in enumerate(board_solder.reference_tiles(board['svg'],prefix=key+'-reference'),1):
            label=ET.fromstring(tile).get('data-fragment')
            parts.append('<figure class="board-print-tile"><figcaption>'+_esc(board['ref'])+' · сторона деталей · фрагмент '+str(i)+' · '+_esc(label)+'</figcaption>'+tile+'</figure>')
        parts.append('</div>')
    regions=board_solder.grid_regions(layout['columns'],layout['rows'])
    if len(regions)>1:
        parts.append('<div class="board-print board-print-tiles"><h4>Крупные фрагменты стороны пайки</h4>')
        for i,region in enumerate(regions,1):
            x0,y0,x1,y1=region
            caption=f'{title} · пайка · {i}/{len(regions)} · столбцы {x0}–{x1}, строки {y0}–{y1}'
            parts.append('<figure class="board-print-tile"><figcaption>'+_esc(caption)+'</figcaption>'+
                svg(layout,side='back',title=title,region=region,prefix=key+'-print')+'</figure>')
        parts.append('</div>')
    if plan:
        rows=[]
        for i,op in enumerate(plan['operations'],1):
            contacts='; '.join(board_solder.endpoint(layout,h['at']) for h in layout['holes']
                              if _coord(h['at']) in board_solder.targets(layout,op)['pads'])
            rows.append([str(i),_esc(op['title']+' · '+('пайка' if op['side']=='back' else 'детали')),
                         _esc(op['action'])+'<br>'+_esc(contacts),_esc(op['material']),_esc(op['check'])])
        parts.append('<div class="board-print"><h4>Полный план операций · '+str(len(rows))+'</h4>'+
                     _table(('№','Операция / сторона','Действие / контакты','Материал','Проверка'),rows,'board-print-operations')+'</div>')
        parts.append(heading('source-steps','Исходная последовательность'))
    else: parts.append(heading('solder','Порядок пайки'))
    parts.append('<p>Последовательность из источника платы. Просмотр шага не является отметкой о выполнении.</p><div class="board-tools"><button type="button" data-board-action="step-previous">Предыдущий шаг пайки</button><button type="button" data-board-action="step-next">Следующий шаг пайки</button><output class="board-step-status"></output></div><ol class="board-steps">')
    parts.extend('<li tabindex="0" data-solder-step="'+str(i)+'">'+_esc(s)+'</li>' for i,s in enumerate(board.get('steps',[]),1))
    parts.append('</ol>')
    parts.append(heading('neighbours','Опасные соседние площадки'))
    parts.append(_table(('Площадки','Цепи','Последствие замыкания'),(
        [_esc('('+_coord(a)+') – ('+_coord(b)+')'),_esc(na+' / '+nb),_esc(why)]
        for a,b,na,nb,why in board.get('neighbours',[]))) if board.get('neighbours') else '<p>В данных этой платы отдельный список не задан. Это не подтверждение отсутствия опасных соседей.</p>')
    parts.append(heading('checks','Проверки платы'))
    parts.append(_table(('Что','Как','Проходит, если','Провал означает'),
                        ([_esc(x) for x in row] for row in board.get('checks',[]))))
    if board.get('page'): parts.append('<p>Источник платы: <a href="'+_esc(board['page'])+'">'+_esc(str(board['page']).rsplit('/',1)[-1])+'</a>.</p>')
    parts.append('</section>')
    return ''.join(parts)
