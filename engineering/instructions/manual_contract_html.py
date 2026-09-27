"""HTML views of an already validated operation contract."""
from html import escape
import json


def esc(value):
    return escape(str(value))


def quantity(value):
    return 'не определено' if value is None else str(value)


def table(headers, rows):
    return '<table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+cell+'</td>' for cell in row)+'</tr>' for row in rows)+'</tbody></table>'


def reuse_plan(rows):
    labels={'replace':'Заменить','reuse':'Оставить после проверки','prepare':'Подготовить'}
    if len({r['part'] for r in rows})!=len(rows): raise ValueError('REUSE_DUPLICATE')
    for r in rows:
        if r['action'] not in labels or not r['condition'].strip(): raise ValueError('REUSE_CONDITION')
    return '<section class="reuse-plan"><h2>Что оставить, заменить и допечатать</h2>'+table(
        ['Деталь','Действие','Условие'],[[esc(r['label']),labels[r['action']],esc(r['condition'])] for r in rows])+'</section>'


def overview(doc, report):
    ops={op['id']:op for op in doc['operations']}
    routes=[]
    for stage,order in report['order'].items():
        route=''.join(f'<li><a href="#{key}">{esc(ops[key]["title"])}</a></li>' for key in order)
        routes.append(f'<details data-stage="{esc(stage)}"><summary>{esc(doc["stages"][stage])}: порядок операций</summary><ol>{route}</ol></details>')
    scope=' · '.join(esc(v) for v in doc['scope'].values())
    return ('<section id="manual-scope"><h2>Применимость и порядок</h2><p>'+scope+'</p>'
            '<p>Порядок учитывает доступ к крепежу и проводам. Выберите ступень и следуйте её ссылкам. '
            'Незаполненные измерения и проверки сохраняют запрет зависимых физических действий.</p>'
            +''.join(routes)+'</section>')


def measurements(doc):
    rows=[]
    for key,m in doc['measurements'].items():
        value=quantity(m['value'])
        if isinstance(m['value'],list):
            value=' × '.join(str(v) for v in m['value'])
        rows.append([esc(m['label']),esc(value)+' '+esc(m['unit']),esc(m['provenance']),
                     esc(m['source']),esc(m['obtained_by'] or m['datum'] or '—')])
    return table(['Что','Значение и единица','Происхождение','Источник','Как получить / база отсчёта'],rows)


def hardware(doc):
    return table(['Крепёж / расходник','Количество','Если количество неизвестно'],[
        [esc(p['label']),quantity(p['quantity']),esc(p['obtained_by'] or 'Учтено по операциям')]
        for p in doc['parts'].values() if p['kind']=='hardware'])


def operations(doc, report, pictures):
    by_id={op['id']:op for op in doc['operations']}
    dependencies={key:set().union(*map(set,op['requires'].values())) for key,op in by_id.items()}
    ordered=[]
    while len(ordered)<len(by_id):
        ready=[key for key in by_id if key not in ordered and dependencies[key]<=set(ordered)]
        if not ready:
            # Different valid stage orders can disagree; their explicit routes remain authoritative.
            ordered.extend(key for key in by_id if key not in ordered)
            break
        ordered.extend(ready)
    cards=[]
    for key in ordered:
        op=by_id[key]
        picture=pictures.get(key,{})
        title=(f'Шаг {picture["number"]}. ' if picture else '')+op['title']
        image=(f'<figure><button type="button" data-image-zoom aria-label="Увеличить: {esc(title)}"><img src="{picture["uri"]}" alt="{esc(title)}" data-picture="{esc(picture["name"])}"></button></figure>'
               if picture else '')
        if picture.get('focus'):
            focus=picture['focus']
            image += (f'<figure class="detail-picture"><img src="{focus["uri"]}" data-picture="{esc(focus["name"])}" '
                      f'alt="Место работы: {esc(title)}"><figcaption>{esc(focus["legend"])}. '
                      'Прочие тела скрыты. Показаны поверхности CAD и габариты компонентов; '
                      'клеммы и действительные зазоры проверяются по данным и на деталях.</figcaption></figure>')
        refs='; '.join(esc(doc['parts'][ref]['label']) for ref in op['parts']) or 'Отдельные детали не требуются.'
        count='; '.join(esc(doc['parts'][ref]['label'])+' × '+quantity(q) for ref,q in op['installs'].items())
        links=[]
        for stage,previous in op['requires'].items():
            links.append('<li data-stage="'+esc(stage)+'">'+esc(doc['stages'][stage])+': '+(
                ', '.join(f'<a href="#{p}">{esc(by_id[p]["title"])}</a>' for p in previous) or 'начало')+'</li>')
        missing=report['incomplete'].get(key,[])
        unknown=[]
        for ref in missing:
            m=doc['parts'][ref[5:]] if ref.startswith('part:') else doc['measurements'][ref]
            unknown.append(esc(m['label'])+': '+esc(m['obtained_by']))
        pending=('<details class="warning"><summary>Не хватает данных для этого шага или его предшественников</summary>'
                 '<ul>'+''.join('<li>'+u+'</li>' for u in unknown)+'</ul></details>') if unknown else ''
        navigation=[]
        for stage in op['stages']:
            route=report['order'][stage]; pos=route.index(key); links_nav=[]
            for delta,label in ((-1,'← Назад'),(1,'Далее →')):
                if 0<=pos+delta<len(route):
                    links_nav.append(f'<a href="#{route[pos+delta]}">{label} {esc(by_id[route[pos+delta]]["title"])}</a>')
            if picture:
                links_nav.append(f'<a href="#viewer" data-viewer-step="{picture["number"]}">Этот шаг в 3D</a>')
            navigation.append(f'<nav class="step-navigation" data-stage="{esc(stage)}"><b>Операция {pos+1} из {len(route)}</b>'+ ' · '.join(links_nav)+'</nav>')
        cards.append(f'<article id="{key}" class="card operation" data-operation="{key}">'
            f'<details class="operation-body"><summary>'+''.join(f'<span data-stage="{esc(s)}" data-route-position="{report["order"][s].index(key)+1}">Операция {report["order"][s].index(key)+1} из {len(report["order"][s])}: </span>' for s in op['stages'])+f'<h3 id="{key}-heading">{esc(op["title"])}</h3></summary><p><b>Применяется:</b> '+', '.join(esc(doc['stages'][s]) for s in op['stages'])+
            f'</p><details><summary>До этого шага</summary><ul>{"".join(links)}</ul></details>{image}'
            f'<p><b>Детали:</b> {refs}</p>'+ (f'<p><b>Крепёж:</b> {count}</p>' if count else '')+
            f'<p><b>Инструменты:</b> {esc("; ".join(op["tools"]) or "Не требуются.")}</p>'
            f'<p>{esc(op["action"])}</p><p><b>Проверка:</b> {esc(op["check"])}</p>'
            f'<p class="note"><b>Если не прошла:</b> {esc(op["fail"])}</p>'
            f'<p class="warning">{esc(op["permission"])}</p>{pending}{"".join(navigation)}</details></article>')
    return ('<section id="pictured-operations"><h2>Шаги с картинками: сборка и электрика</h2>'
            '<p>Картинки показывают геометрию шага; серое уже установлено, цветное добавляется. '
            'Проверка результата выполняется отдельно. Пустые измерения не считаются пройденными.</p>'
            +''.join(cards)+'</section>')


def embedded(doc, report):
    data=json.dumps({'contract':doc,'validation':report},ensure_ascii=False,sort_keys=True,allow_nan=False).replace('<','\\u003c')
    return '<script type="application/json" id="manual-contract">'+data+'</script>'
