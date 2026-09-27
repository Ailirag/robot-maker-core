"""Self-contained calculation receipt; operational instructions stay primary."""
from base64 import b64encode
from html import escape
import json
import re
from engineering import board_model,board_search

LABELS={'SOLVED':'размещение найдено','SEARCH_LIMIT':'достигнут лимит поиска',
        'INFEASIBLE':'в заданных ограничениях размещения нет',
        'INPUTS_INCOMPLETE':'не хватает исходных данных','INVALID':'ошибка исходных данных'}

def render(value,result,*,filename_prefix='board'):
    if not isinstance(filename_prefix,str) or not re.fullmatch(r'[a-zA-Z0-9_-]+',filename_prefix):
        raise ValueError('BOARD_AUTOMATION: invalid filename prefix')
    value=board_model.validate(value)
    if not isinstance(result,dict) or result.get('schema')!=board_search.SCHEMA or result.get('status') not in LABELS:
        raise ValueError('BOARD_AUTOMATION: unsupported calculation result')
    def encoded(data):
        return json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    def download(name,data):
        payload=b64encode(encoded(data).encode()).decode()
        return f'<a download="{name}" href="data:application/json;base64,{payload}">{escape(name)}</a>'
    status=result['status']
    rows=[]
    for item in result['issues']:
        rows.append('<tr><td>'+escape(item.get('path',''))+'</td><td>'+escape(item['code'])+'</td><td>'+
                    escape(item.get('obtained_by') or item.get('detail') or item.get('fact_id') or 'Исправить указанный вход.')+'</td></tr>')
    best=result.get('best')
    summary=f'<p>Проверено вариантов: {result["visited"]}; лимит: {result["settings"]["max_nodes"]}. '
    summary+='Перебор завершён.' if result['exhaustive'] else 'Полный перебор не завершён.'
    summary+='</p>'
    if best:
        summary+=f'<p>Лучший найденный вариант: {best["score"][0]} изолированных перемычек; суммарный размах по сетке — {best["score"][1]} шагов. Размах не задаёт длину отрезка провода.</p>'
    if result['optimal_within_model']:
        summary+='<p>Лучший результат среди заданных положений и поворотов при этом алгоритме соединений; глобальная оптимальность трассировки не доказана.</p>'
    return ('<details class="board-automation"><summary>Автоматический подбор: '+LABELS[status]+'</summary>'+
        '<p><code>'+status+'</code>. Расчёт не заменяет примерку, проверку монтажа и допуск к питанию.</p>'+summary+
        ('<table><thead><tr><th>Вход</th><th>Причина</th><th>Как уточнить</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>' if rows else '')+
        '<p>Исходная модель и результат сохранены внутри этого файла: '+download(filename_prefix+'-model.json',value)+' · '+download(filename_prefix+'-search.json',result)+'</p>'+
        '<p>Хеш входов: <code>'+escape(result['input_sha256'])+'</code>.</p>'+
        '<script type="application/json" class="board-search-result">'+encoded(result).replace('<','\\u003c')+'</script></details>')
