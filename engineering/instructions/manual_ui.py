"""Offline navigation, reference snapshots and versioned notes for a manual."""
from __future__ import annotations

import base64
from hashlib import sha256


def digest(*parts: str) -> str:
    """SHA-256 of the parts in order, without joining them.

    Same value as hashing ``"".join(parts)``. A guide that embeds its offline
    viewer is tens of megabytes, and joining it only to hash it allocated a
    second full copy of the page: that is where the freshness gate died with
    `MemoryError: Unable to allocate output buffer` on a machine with tens of
    gigabytes free.
    """
    accumulator = sha256()
    for part in parts:
        accumulator.update(part.encode("utf-8"))
    return accumulator.hexdigest()
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ASSETS = Path(__file__).with_name('assets')
SCRIPT = '\n'.join((ASSETS / name).read_text(encoding='utf-8') for name in ('manual.js','outline.js','board.js','workshop.js'))
STYLE = '\n'.join((ASSETS / name).read_text(encoding='utf-8') for name in ('manual.css','outline.css','board.css'))


def controls(contract, *, stages=None):
    options = ''.join('<option value="'+escape(k)+'">'+escape(v)+'</option>'
                      for k,v in contract['stages'].items()) if contract else ''
    notes = ('<label>Ступень сборки <select id="manual-stage">'+options+'</select></label>'
        '<p id="manual-progress"></p><p>Личная отметка не является протоколом приёмки '
        'и не снимает физических запретов. Для переноса записей сохраните JSON; '
        'другая версия инструкции не принимает его автоматически.</p>'
        '<button type="button" id="progress-export">Экспорт записей</button>'
        '<label class="import-label">Импорт записей <input id="progress-import" type="file" '
        'accept=".json,application/json"></label>'
        '<details id="progress-transfer"><summary>Перенос записей текстом</summary>'
        '<p>Здесь можно сохранить или вставить JSON, если браузер не скачивает файлы.</p>'
        '<textarea id="progress-text" aria-label="JSON личных записей" rows="8"></textarea>'
        '<a id="progress-download" class="dl" hidden>Скачать JSON</a>'
        '<button id="progress-apply" type="button">Загрузить JSON из текста</button></details>') if contract else ''
    if stages and contract is None:
        options = ''.join('<option value="'+escape(k)+'">'+escape(v)+'</option>' for k,v in stages.items())
        notes = ('<label>Ступень электросборки <select id="manual-stage">'+options+'</select></label>'
                 '<p>Схемы, платы и порядок соединений ниже относятся к выбранной ступени. '
                 'Переключение не подтверждает состояние реальной сборки и не снимает физических запретов.</p>')
    return ('<div class="print-toolbar" id="print-toolbar" hidden>'
        '<b>Печатный вид</b> <span>Состав соответствует выбранной ступени. '
        'Текстовые источники остаются во вкладке «Инженерное» и в рабочую распечатку не входят. '
        'Печать/PDF доступны через меню поддерживаемого браузера.</span>'
        '<button type="button" id="print-now">Печать / сохранить PDF</button>'
        '<button type="button" id="print-return">Вернуться к инструкции</button></div>'
        '<section class="manual-controls" aria-label="Управление инструкцией">'+notes+
        '<button type="button" id="print-manual">Печатный вид инструкции</button>'
        '<button type="button" id="print-solder">Листы для пайки</button>'
        '<p id="manual-message" role="status" aria-live="polite"></p></section>')


class Sections(HTMLParser):
    """Add stable heading anchors and an outline without rewriting embedded assets."""
    def __init__(self, prefix, legacy_anchors=None):
        super().__init__(convert_charrefs=False)
        self.prefix, self.output, self.headings = prefix, [], []
        self.capture = None
        self.counter = 0
        self.stack = []
        self.parent_key = ''
        self.identities = {}
        if legacy_anchors is not None and (not isinstance(legacy_anchors,dict) or
            any(not isinstance(k,str) or not k or not isinstance(v,str) or not v
                for k,v in legacy_anchors.items())):
            raise ValueError('MANUAL_ANCHOR: expected non-empty alias and target strings')
        self.legacy_anchors = legacy_anchors

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        attrs = dict(attrs)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(attrs.get('data-stage'))
        if tag in ('h2','h3'):
            stage = next((stage for stage in reversed(self.stack) if stage), None)
            if tag == 'h2': self.counter += 1
            self.heading = {'tag':tag,'stage':stage,'id':attrs.get('id'),
                            'label':attrs.get('data-nav-label'),'position':len(self.output),
                            'legacy':f'{self.prefix}-section-{self.counter}' if tag=='h2' else None}
            self.capture = []
        self.output.append(raw)

    def handle_startendtag(self, tag, attrs):
        self.output.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        self.output.append('</'+tag+'>')
        if self.stack: self.stack.pop()
        if self.capture is not None and tag == self.heading['tag']:
            h = self.heading
            title = unescape(''.join(self.capture))
            identity = '|'.join((self.prefix,h['stage'] or '',tag,
                                 self.parent_key if tag=='h3' else '',title))
            duplicate = self.identities.get(identity,0)
            self.identities[identity] = duplicate+1
            key = h['id'] or self.prefix+'-heading-'+sha256((identity+('|'+str(duplicate) if duplicate else '')).encode()).hexdigest()[:14]
            if tag == 'h2': self.parent_key = key
            position = h['position']
            raw = self.output[position]
            if not h['id']: raw = raw[:-1]+' id="'+escape(key)+'">'
            aliases = ([h['legacy']] if h['legacy'] and h['legacy'] != key else []) if self.legacy_anchors is None else [alias for alias,target in self.legacy_anchors.items() if target==key]
            for alias in aliases:
                raw = '<span class="legacy-anchor" id="'+escape(alias)+'" data-anchor-target="'+escape(key)+'"></span>'+raw
            self.output[position] = raw
            self.headings.append((key,h['label'] or title,h['stage'],tag))
            self.capture = None

    def handle_data(self, data):
        self.output.append(data)
        if self.capture is not None: self.capture.append(data)

    def handle_entityref(self, name): self.handle_data('&'+name+';')
    def handle_charref(self, name): self.handle_data('&#'+name+';')
    def handle_comment(self, data): self.output.append('<!--'+data+'-->')
    def handle_decl(self, data): self.output.append('<!'+data+'>')

    def render(self, source):
        self.feed(source)
        if self.legacy_anchors is not None:
            targets={row[0] for row in self.headings}
            if (not isinstance(self.legacy_anchors,dict) or
                any(not isinstance(alias,str) or not alias or alias in targets or target not in targets
                    for alias,target in self.legacy_anchors.items())):
                raise ValueError('MANUAL_ANCHOR: invalid alias or missing target')
        groups=[]
        for key,title,stage,tag in self.headings:
            link='<a href="#'+escape(key)+'">'+escape(title)+'</a>'
            attrs=(' data-stage="'+escape(stage)+'"' if stage else '')
            if tag=='h2' or not groups:
                groups.append([link,attrs,[]])
            else:
                groups[-1][2].append('<li'+attrs+'>'+link+'</li>')
        rows=[]
        for link,attrs,children in groups:
            item=('<details class="outline-group"><summary>'+link+'</summary><ol>'+''.join(children)+'</ol></details>') if children else link
            rows.append('<li'+attrs+'>'+item+'</li>')
        outline=('<aside class="manual-outline" id="'+self.prefix+'-outline" aria-label="Оглавление вкладки">'
                 '<div class="outline-header"><div class="outline-title"><b>В этой вкладке</b><button type="button" class="outline-close">Закрыть оглавление</button></div>'
                 '<p class="outline-stage"></p></div><ol>'+''.join(rows)+'</ol></aside>')
        return outline+'<div class="tab-content">'+''.join(self.output)+'</div>'


def artifact_uri(candidate, relative, digest, mime):
    candidate = Path(candidate).resolve()
    path = (candidate / relative).resolve()
    if not path.is_relative_to(candidate):
        raise ValueError('MANUAL_ARTIFACT_PATH: outside candidate')
    data = path.read_bytes()
    if sha256(data).hexdigest() != digest:
        raise ValueError('MANUAL_ARTIFACT_HASH: '+relative)
    return 'data:'+mime+';base64,'+base64.b64encode(data).decode('ascii')


def references(root, rows):
    blocks, anchors = [], {}
    for number, row in enumerate(rows, 1):
        if set(row) != {'path','label'}:
            raise ValueError('MANUAL_REFERENCE: expected path and label')
        path = (root / row['path']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('MANUAL_REFERENCE_PATH: outside project')
        data = path.read_bytes()
        content = '\n'.join(line.rstrip() for line in data.decode('utf-8-sig').splitlines())
        # The download preserves raw source bytes; presentation uses the HTML's LF.
        key = 'reference-'+str(number)
        anchors[path] = key
        uri = 'data:text/plain;charset=utf-8;base64,'+base64.b64encode(data).decode('ascii')
        blocks.append('<section id="'+key+'" class="offline-reference"><h2>'+escape(row['label'])+
            '</h2><p>Встроенная копия источника на момент генерации. Не изменяет его вердикты.</p>'
            '<p class="hash">'+escape(row['path'])+' · SHA-256 '+sha256(data).hexdigest()+'</p>'
            '<a class="dl" href="'+uri+'" download="'+escape(path.name)+'">Скачать источник</a>'
            '<details><summary>Читать источник внутри файла</summary><pre>'+escape(content)+'</pre></details></section>')
    return ''.join(blocks), anchors


class ReferenceLinks(HTMLParser):
    def __init__(self, base, anchors):
        super().__init__(convert_charrefs=False)
        self.base, self.anchors, self.output = base, anchors, []

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href'):
            href = attrs['href']
            parsed = urlsplit(href)
            if not parsed.scheme and not href.startswith('#'):
                key = self.anchors.get((self.base / unquote(parsed.path)).resolve())
                if key:
                    raw = raw.replace('href="'+escape(href, quote=True)+'"', 'href="#'+key+'"', 1)
        self.output.append(raw)

    def handle_startendtag(self, tag, attrs): self.output.append(self.get_starttag_text())
    def handle_endtag(self, tag): self.output.append('</'+tag+'>')
    def handle_data(self, data): self.output.append(data)
    def handle_entityref(self, name): self.output.append('&'+name+';')
    def handle_charref(self, name): self.output.append('&#'+name+';')
    def handle_comment(self, data): self.output.append('<!--'+data+'-->')
    def handle_decl(self, data): self.output.append('<!'+data+'>')

    def render(self, source):
        self.feed(source)
        return ''.join(self.output)
