"""A solder-side view of an existing perfboard layout; no circuit inference."""
from hashlib import sha256
from html import escape


def render(*, columns, rows, holes, links, pins, title):
    def valid(point):
        return (len(point)==2 and all(type(n) is int for n in point)
                and 0<=point[0]<columns and 0<=point[1]<rows)
    if type(columns) is not int or type(rows) is not int or min(columns,rows)<1:
        raise ValueError('BOARD: invalid grid')
    coordinates=list(holes)+[p for ref in pins.values() for p in ref.values()]
    coordinates += [p for _,a,b,_ in links for p in (a,b)]
    if any(not valid(p) for p in coordinates):
        raise ValueError('HOLE: outside the declared grid')
    def position(point):
        c,r=point
        return (52+(columns-1-c)*28,64+r*28)
    def colour(net):
        palette = ('#1f4e79','#8b1e3f','#336b33','#63318c','#874800','#006b70','#6b4f4f','#333333')
        return '#777777' if net is None else palette[int(sha256(net.encode('utf-8')).hexdigest()[:6],16) % len(palette)]
    def pair(point): return str(point[0])+','+str(point[1])
    width,height=104+(columns-1)*28,116+(rows-1)*28
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
         f'<title>{escape(title)}: сторона пайки, верх сохранён, столбцы зеркальны</title>',
         '<rect width="100%" height="100%" fill="white"/>',
         f'<text x="{width/2}" y="18" text-anchor="middle" font-size="14">ВЕРХ ПЛАТЫ — СТОРОНА ПАЙКИ</text>']
    for c in range(columns):
        x,y=position((c,0))
        out.append(f'<text x="{x}" y="43" text-anchor="middle" font-size="11">{c}</text>')
    for r in range(rows):
        x,y=position((0,r))
        out.append(f'<text x="24" y="{y+4}" text-anchor="middle" font-size="11">{r}</text>')
    for c in range(columns):
        for r in range(rows):
            x,y=position((c,r))
            out.append(f'<circle cx="{x}" cy="{y}" r="3" fill="#eee" stroke="#aaa"/>')
    for net,a,b,kind in links:
        x1,y1=position(a); x2,y2=position(b)
        if kind not in ('wire','bridge','jumper'):
            raise ValueError('LINK: unknown conductor kind')
        dash=' stroke-dasharray="5 4"' if kind=='jumper' else ''
        stroke=5 if kind=='wire' else 2
        out.append(f'<line data-net="{escape(net)}" data-from="{pair(a)}" data-to="{pair(b)}" data-kind="{kind}" '
                   f'x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{colour(net)}" stroke-width="{stroke}"{dash}>'
                   f'<title>{escape(net)}: {pair(a)} → {pair(b)}, {kind}</title></line>')
    pin_labels={}
    for ref,terminals in pins.items():
        for pin,point in terminals.items():
            pin_labels.setdefault(point,[]).append(str(ref)+'.'+str(pin))
    for point,net in sorted(holes.items()):
        x,y=position(point)
        label='; '.join(pin_labels.get(point,[]))
        out.append(f'<circle data-hole="{pair(point)}" data-net="{escape(net or "")}" cx="{x}" cy="{y}" r="4" '
                   f'fill="white" stroke="{colour(net)}" stroke-width="2"><title>({pair(point)}) '
                   f'{escape(net or "не подключено")} {escape(label)}</title></circle>')
    out.append(f'<text x="{width/2}" y="{height-17}" text-anchor="middle" font-size="12">НЕ МАСШТАБ 1:1 · координаты исходной стороны деталей</text></svg>')
    return ''.join(out)


def connection_table(links):
    labels={'wire':'голый лужёный провод, пайка на площадках',
            'bridge':'мостик припоя','jumper':'изолированная перемычка'}
    return ('<table><thead><tr><th>Цепь</th><th>Отверстия (столбец, ряд)</th><th>Соединение</th></tr></thead><tbody>'+
            ''.join('<tr><td>'+escape(net)+'</td><td>'+escape(str(a)+' → '+str(b))+'</td><td>'+
                    labels[kind]+'</td></tr>' for net,a,b,kind in links)+'</tbody></table>')
