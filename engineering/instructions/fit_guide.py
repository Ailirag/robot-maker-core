"""Standalone review and partial-observation form for per-interface fit checks."""
from __future__ import annotations
from html import escape
import json

LABELS = {"PASS":"Подтверждено", "FAIL":"Не выполнено", "MISSING":"Не проверено",
          "UNBOUND":"Нет привязки примерки", "CONTEXT_MISMATCH":"Другой образец или процесс",
          "STALE":"Запись изменилась", "UNCERTAIN":"Недостаточно точности",
          "INVALID":"Некорректная запись", "WRONG_SCOPE":"Другая область измерения",
          "WRONG_UNIT":"Другая единица", "INSUFFICIENT_PROVENANCE":"Неподтверждённое происхождение",
          "DATUM_MISMATCH":"Другая база измерения", "AMBIGUOUS_DATUM":"Нет базы измерения",
          "CHECKS_PASSED":"Проверки плана выполнены", "CHECKS_FAILED":"Есть неудачная примерка",
          "CHECKS_PENDING":"Остаются проверки", "CHECKS_INVALID":"Проверьте записи"}


def render(report, *, introduction=None, scenarios=(), guidance=None, value_labels=None):
    h = lambda s: escape(str(s), quote=True)
    guidance = guidance or {}
    value_labels = value_labels or {}
    intro = introduction or "Проверьте только оставшиеся пункты. Успешные записи повторно вводить не нужно."
    links = "".join('<a href="#group-'+h(g["id"])+'">'+h(g["title"])+'</a>' for g in report["groups"])
    sections = []
    for group in report["groups"]:
        rows = []
        for c in group["criteria"]:
            expectation = ("да" if c["expected"] else "нет") if c["kind"]=="boolean" else (value_labels.get(c["expected"], c["expected"]) if c["kind"]=="choice" else f'{c["minimum"]:g}…{c["maximum"]:g} мм')
            actual = "—" if c["value"] is None else ("да" if c["value"] is True else "нет" if c["value"] is False else str(c["value"]))
            rows.append('<tr><th scope="row">'+h(c["label"])+'</th><td>'+h(expectation)+'</td><td>'+h(value_labels.get(actual,actual))+'</td>'
                        '<td class="'+h(c["state"])+'">'+h(LABELS.get(c["state"],c["state"]))+'</td></tr>')
        form = []
        for q in report["requests"]:
            if group["id"] != q["groups"][0]:
                continue
            key = q["id"]
            label = '<label for="obs-'+key+'">'+h(q["label"])+'</label>'
            if q["kind"]=="boolean":
                control = '<select id="obs-'+key+'" data-request="'+key+'"><option value="">Не проверено</option><option value="true">Да</option><option value="false">Нет</option></select>'
            elif q["kind"]=="choice":
                control = '<select id="obs-'+key+'" data-request="'+key+'"><option value="">Не проверено</option>'+''.join('<option value="'+h(v)+'">'+h(value_labels.get(v,v))+'</option>' for v in q["choices"])+'</select>'
            else:
                control = '<input id="obs-'+key+'" data-request="'+key+'" inputmode="decimal" placeholder="мм">'
                control += '<label>Погрешность, мм (если известна)<input data-uncertainty="'+key+'" inputmode="decimal"></label><label>Прибор<input data-instrument="'+key+'"></label>'
            form.append('<div class="observation">'+label+control+'<p>'+h(q["obtained_by"])+'</p>'
                        '<label class="confirm"><input type="checkbox" data-confirm="'+key+'"> Проверен указанный узел и образец в описанных условиях</label>'
                        '<label>Примечание<textarea data-note="'+key+'" rows="2"></textarea></label></div>')
        meaning = "Результат относится только к указанному образцу." if group["role"]=="reference" else "Проверить при сборке этого узла; численные замеры нужны только там, где они указаны."
        context = '<dl>'+''.join('<dt>'+h(k)+'</dt><dd>'+h(v)+'</dd>' for k,v in group["context"].items())+'</dl>'
        sections.append('<section id="group-'+h(group["id"])+'"><h2>'+h(group["title"])+'</h2><p>'+h(meaning)+'</p>'
                        '<p>'+h(guidance.get(group["id"],""))+'</p><div class="table-wrap"><table><thead><tr><th>Критерий</th><th>Нужно</th><th>Наблюдение</th><th>Результат</th></tr></thead><tbody>'
                        +''.join(rows)+'</tbody></table></div>'+''.join(form)+'<details><summary>Образец и основания критериев</summary>'+context+
                        '<ul>'+''.join('<li>'+h(c["label"])+': '+h(c["rationale"])+'</li>' for c in group["criteria"])+'</ul></details></section>')
    scenario_html = ""
    if scenarios:
        scenario_html = '<section id="scenarios"><h2>Оценка отклонений</h2><p>Расчётные сценарии, не замеры и не приёмка посадок.</p><ul>'+''.join('<li>'+h(s)+'</li>' for s in scenarios)+'</ul></section>'
    payload = json.dumps(report,ensure_ascii=False,allow_nan=False).replace("<","\\u003c")
    return '''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>'''+h(report["title"])+'''</title><style>
*{box-sizing:border-box}body{margin:0;background:#f0f3f6;color:#192b36;font:16px/1.5 system-ui,sans-serif}
nav{position:fixed;inset:0 auto 0 0;width:235px;background:#183642;color:white;padding:24px 16px;overflow:auto}
nav a{display:block;color:#e2f3f5;padding:8px 0}main{margin-left:235px;padding:28px;max-width:1250px}
section{background:white;padding:24px;margin-bottom:20px;border-radius:12px;scroll-margin-top:15px}
h1,h2{line-height:1.25}h1{font-size:30px}h2{font-size:23px}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%}
td,th{padding:10px;border-bottom:1px solid #d4dfe2;text-align:left;vertical-align:top}.PASS{color:#146e39}.FAIL,.INVALID{color:#a32620}
label{display:block;margin:10px 0}input,textarea,select,button{font:inherit;padding:9px;border:1px solid #7c949e;border-radius:5px}
textarea{display:block;width:100%}select{margin-left:8px}input[type=checkbox]{width:20px;height:20px;vertical-align:middle}
.observation{margin:15px 0;padding:15px;border:1px solid #c5d6dd;border-radius:8px}.observation p{color:#415966}
button{background:#0a6175;color:white;cursor:pointer}details{margin-top:20px}dd{overflow-wrap:anywhere;margin-bottom:10px}dt{font-weight:bold}
.status{padding:14px;background:#fff2d7;border-left:4px solid #b77e12}#message{white-space:pre-wrap}
@media(max-width:820px){nav{position:sticky;top:0;inset:auto;width:100%;padding:8px;z-index:2;max-height:150px}nav a{display:inline-block;margin:0 10px}nav strong{display:none}main{margin:0;padding:12px}section{padding:16px;scroll-margin-top:160px}h1{font-size:25px}}
@media print{nav,.observation,#receipt,button{display:none}main{margin:0;padding:0}section{break-inside:avoid}}
</style></head><body><nav><strong>Посадки и допуски</strong><a href="#overview">Что делать сейчас</a>'''+links+'''<a href="#receipt">Сохранить наблюдения</a><a href="#provenance">Основания</a></nav><main>
<section id="overview"><h1>'''+h(report["title"])+'''</h1><p>'''+h(intro)+'''</p><p class="status">'''+h(LABELS[report["state"]])+'''</p>
<p>Результат этого плана не является разрешением на выпуск, питание или движение.</p></section>'''+scenario_html+''.join(sections)+'''
<section id="receipt"><h2>Сохранить только новые наблюдения</h2><p>Оставьте непроверенные пункты пустыми. Данные сохраняются в файл на вашем устройстве; отправки по сети нет.</p>
<label>Дата проверки<input id="recorded-at" type="date"></label><label>Кто проверил<input id="operator" autocomplete="name"></label>
<button type="button" id="save">Сохранить JSON</button><p id="message" role="status" aria-live="polite"></p></section>
<section id="provenance"><h2>Основания</h2><p>Примерки привязаны к фактам и контексту. Новые значения требуют проверки и внесения в реестр; кнопка сохранения не утверждает результат.</p>
<details><summary>Машинный отчёт</summary><pre id="report-text"></pre></details></section></main>
<script id="report" type="application/json">'''+payload+'''</script><script>
"use strict";
const report = JSON.parse(document.getElementById("report").textContent);
document.getElementById("report-text").textContent = JSON.stringify(report, null, 2);
document.getElementById("save").addEventListener("click", () => {
  const message = document.getElementById("message");
  try {
    const observations = {};
    for (const q of report.requests) {
      const field = document.querySelector('[data-request="'+q.id+'"]');
      const raw = field.value.trim();
      if (!raw) continue;
      if (!document.querySelector('[data-confirm="'+q.id+'"]').checked) throw Error("Подтвердите контекст: "+q.label);
      let value;
      if (q.kind === "boolean") value = raw === "true";
      else if (q.kind === "choice") value = raw;
      else {
        value = Number(raw.replace(",","."));
        if (!Number.isFinite(value)) throw Error("Нужно число: "+q.label);
      }
      const row = {value, context_confirmed:true, note:document.querySelector('[data-note="'+q.id+'"]').value.trim()};
      if (q.kind === "range") {
        row.instrument = document.querySelector('[data-instrument="'+q.id+'"]').value.trim();
        if (!row.instrument) throw Error("Укажите прибор: "+q.label);
        const rawU = document.querySelector('[data-uncertainty="'+q.id+'"]').value.trim();
        const u = rawU ? Number(rawU.replace(",",".")) : null;
        if (u !== null && (!Number.isFinite(u) || u < 0)) throw Error("Проверьте погрешность: "+q.label);
        row.uncertainty = {value:u, obtained_by:u===null ? "Уточнить погрешность измерения" : "Указана оператором"};
      }
      observations[q.id] = row;
    }
    const recorded_at = document.getElementById("recorded-at").value;
    const operator = document.getElementById("operator").value.trim();
    if (!recorded_at || !operator || !Object.keys(observations).length) throw Error("Нужны дата, исполнитель и хотя бы одно новое наблюдение.");
    const receipt = {schema:"print-fit-observations-v1",report_digest:report.report_digest,recorded_at,operator,observations};
    const url = URL.createObjectURL(new Blob([JSON.stringify(receipt,null,2)],{type:"application/json"}));
    const a = document.createElement("a"); a.href=url; a.download="FIT_OBSERVATIONS.json"; a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    message.textContent = "Файл сохранён. Он содержит наблюдения для проверки, а не разрешение на сборку или запуск.";
  } catch(error) { message.textContent = error.message; }
});
</script></body></html>'''
