"""Cable planning tables for self-contained instructions; no project knowledge."""
from html import escape as e

def length(value):
    return "Не определена" if value is None else f"{value:.1f} мм"

def render(report,specs,*,suggestions=(),topology=()):
    specs={s["id"]:s for s in specs};suggestions={s["id"]:s for s in suggestions}
    rows=[]
    for row in report["cables"]:
        spec=specs[row["id"]];suggestion=suggestions.get(row["id"],{})
        route_length=row["route_length_mm"]
        estimate=suggestion.get("length_mm")
        geometry=length(route_length) if route_length is not None else (
            length(estimate)+" · предварительный коридор" if estimate is not None else "Не определена")
        issues=list(row["issues"])+list(suggestion.get("issues",[]))
        actions=[str(x) for x in issues]+[v["obtained_by"] for v in row["unknowns"].values()]
        rows.append("<tr><td>"+e(row["id"])+"</td><td>"+e(spec.get("net",""))+
            "</td><td>"+e(spec.get("source",""))+" → "+e(spec.get("target",""))+
            "</td><td>"+e(geometry)+"</td><td>"+e(length(row["cut_length_mm"]))+
            "</td><td><details><summary>"+e(row["status"])+" · что уточнить</summary><ul>"+
            "".join("<li>"+e(s)+"</li>" for s in actions)+"</ul></details></td></tr>")
    missing="".join("<li><b>"+e(t["id"])+"</b>: "+e(t["reason"])+"</li>" for t in topology)
    issues="".join("<li>"+e(p)+"</li>" for p in report["issues"])
    return ('<section id="cable-table"><h2>Кабели: длины и незакрытые данные</h2>'
        '<p>Все размеры в миллиметрах. Маршрут — длина осевой линии с дугами. Для отрезания '
        'нужны подтверждённые выходы контактов, диаметр, допустимый изгиб, зазор и припуски. '
        'Запас для обслуживания считается отдельно и требует проверки свободной петли. '
        'Предварительный коридор не задаёт место реального контакта.</p>'
        '<p><b>'+e(report["status"])+'</b></p><ul>'+issues+'</ul>'
        '<div style="overflow:auto"><table><thead><tr><th>Кабель</th><th>Цепь</th>'
        '<th>Откуда → куда</th><th>Маршрут</th><th>Для отрезания</th><th>Проверка</th>'
        '</tr></thead><tbody>'+"".join(rows)+'</tbody></table></div></section>'
        '<section id="cable-topology"><h2>Соединения, которые ещё нельзя разложить на отрезки</h2>'
        '<ul>'+missing+'</ul></section>')

