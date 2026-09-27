"""Offline measurement instruction for a compensated print trial."""
from __future__ import annotations
import base64
from html import escape
import json


def render(report, meshes, downloads, *, pictures=None):
    plan = report["plan"]
    enc = lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    def link(name, data):
        return '<a class="download" download="'+escape(name,quote=True)+'" href="data:application/octet-stream;base64,'+base64.b64encode(data).decode()+'">'+escape(name)+'</a>'
    files = "".join(link(name, data) for name, data in downloads.items())
    rows = []
    targets = report.get("control_targets", plan["control_targets"])
    for row in targets:
        key = escape(row["id"], quote=True)
        rows.append(f'<tr><td><label for="m-{key}">{escape(row.get("label",row["id"]))}</label></td>'
                    f'<td>{row["nominal_mm"]:g}</td><td>±{row["tolerance_mm"]:g}</td>'
                    f'<td><input id="m-{key}" data-measure="{key}" type="number" step="any" min="0" inputmode="decimal" aria-label="{key}, мм"></td></tr>')
    parts = "".join(f'<tr><td>{escape(report.get("part_names",{}).get(p["id"],p["id"]))}</td><td>{p["nominal_size_mm"]}</td><td>{p["print_size_mm"]}</td></tr>' for p in report["parts"])
    settings = report["project_summary"]
    setting_rows = "".join(f'<tr><th>{escape(k)}</th><td>{escape(str(v))}</td></tr>' for k,v in settings.items())
    instructions = "".join("<li>"+escape(v)+"</li>" for v in report.get("measurement_notes", [
        "Печатать контрольный проект из этой инструкции тем же материалом.",
        "Дождаться остывания, записать инструмент и измерить указанные размеры.",
        "Сохранить результаты кнопкой ниже. Отсутствующий замер остаётся неизвестным.",
    ]))
    views = "".join('<figure><img style="max-width:100%" alt="'+escape(name,quote=True)+'" src="data:image/svg+xml;base64,'+base64.b64encode(data).decode()+'"><figcaption>'+escape(name)+'</figcaption></figure>' for name,data in (pictures or {}).items())
    factors = plan["factors"]
    return """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + escape(report["title"]) + """</title><style>
*{box-sizing:border-box}body{margin:0;background:#eef2f6;color:#172334;font:16px/1.55 system-ui,sans-serif}
nav{position:fixed;width:220px;padding:26px 18px;background:#172334;color:white;height:100vh;overflow:auto}
nav a{display:block;color:#dbeafe;margin:14px 0}main{margin-left:240px;max-width:1100px;padding:24px}
section{background:white;border:1px solid #d7e0eb;border-radius:12px;padding:22px;margin-bottom:22px;scroll-margin-top:18px}
h1,h2{line-height:1.2}.status{padding:12px;background:#fff1ce;border-left:5px solid #bb7b00}table{border-collapse:collapse;width:100%;margin:14px 0}
th,td{text-align:left;border-bottom:1px solid #dce3ec;padding:9px;vertical-align:top}input,textarea,button{font:inherit;padding:8px;border:1px solid #9aabc1;border-radius:5px}
input[type=number]{width:125px}button,.download{display:inline-block;background:#075e8b;color:white;padding:10px 14px;border-radius:6px;margin:6px;text-decoration:none;cursor:pointer}
canvas{width:100%;height:460px;border:1px solid #ccd8e5;background:#f6f9fc;touch-action:none}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
small{color:#45566d}.legend{display:flex;flex-wrap:wrap;gap:14px}.dot{display:inline-block;width:14px;height:14px;margin-right:6px}
@media(max-width:800px){nav{position:sticky;top:0;width:auto;height:auto;padding:8px;z-index:2}nav a{display:inline-block;margin:4px 10px}nav strong{display:none}main{margin:0;padding:12px}section{padding:14px;scroll-margin-top:65px}table{font-size:13px}}
@media print{nav,.download,button,.screen-only{display:none}main{margin:0;padding:0}section{break-inside:avoid;border:none;padding:8px}canvas{height:320px}input{border:0;border-bottom:1px solid #555}}
</style><nav><strong>Контроль печати</strong><a href="#overview">Что проверяем</a><a href="#print">Проект печати</a><a href="#parts">Геометрия</a><a href="#measure">Замеры</a><a href="#source">Происхождение</a></nav><main>
<section id="overview"><h1>""" + escape(report["title"]) + """</h1>
<p class="status">Контрольный образец · """ + escape(plan["status"]) + """.
Это проверка предлагаемой поправки. Приёмка корпуса и физические разрешения отдельно.</p>
<p>Поправка внесена в печатную геометрию: X ×""" + f"{factors[0]:.8f}" + """, Y ×""" + f"{factors[1]:.8f}" + """, Z ×1.
В слайсере оставить масштаб модели и Shrinkage 100%, XY-компенсации 0.
Номинальные размеры для замера указаны ниже; увеличенные размеры STL не являются целевыми размерами остывшей детали.</p>
<ol>""" + instructions + """</ol></section>
<section id="print"><h2>Проект печати</h2><p>Предпочтительный файл — PRINT_JOB.3mf: в нём уже сохранены геометрия и исходные настройки. STL требует переноса этих настроек вручную.</p>""" + files + """<table>""" + setting_rows + """</table>
<small>Здесь сохранённые уставки проекта; это не телеметрия принтера. Физический производитель пластика: """ + escape(plan["material"]["manufacturer"]) + """.</small></section>
<section id="parts"><h2>Печатная раскладка</h2><p class="screen-only">Потяните мышью или пальцем, чтобы повернуть. Колесо меняет масштаб.</p>
<canvas id="view" aria-label="Трёхмерная раскладка контрольных деталей"></canvas><button id="top">Вид сверху</button><button id="iso">Объёмный вид</button><div class="legend" id="legend"></div>
<table><tr><th>Деталь</th><th>Номинальный габарит, мм</th><th>Печатный габарит, мм</th></tr>""" + parts + """</table><details><summary>Виды сверху, снизу, сбоку и разрез</summary>""" + views + """</details></section>
<section id="measure"><h2>Замеры после остывания</h2><p>Пустые клетки означают «не измерено». Числа допусков — критерии этого контрольного плана, а не погрешность вашего инструмента.</p>
<p><label>Инструмент <input id="instrument"></label> <label>Дата/время замера <input id="measured-at" type="datetime-local"></label></p>
<table><tr><th>Что измерить</th><th>Цель, мм</th><th>Допуск, мм</th><th>Получено, мм</th></tr>""" + "".join(rows) + """</table>
<p><label><input type="checkbox" id="cooled"> Детали остыли до комнатной температуры</label></p>
<p><label><input type="checkbox" id="job-confirmed"> Измерены детали именно из PRINT_JOB.3mf этой инструкции</label></p>
<p><label>Наблюдения по посадкам, перекосу, проворачиванию и трещинам<br><textarea id="notes" rows="4" style="width:100%"></textarea></label></p>
<button id="save">Сохранить результаты JSON</button><button onclick="window.print()">Печать инструкции</button><p id="feedback" role="status"></p>
<small>Форма сохраняет наблюдения. Статус CONTROL_VERIFIED вычисляет ядро после проверки полного набора фактов и привязки к файлу; кнопка не утверждает калибровку.</small></section>
<section id="source"><h2>Происхождение и проверки</h2><p>Все вложения находятся внутри этого HTML. Номинальные STEP/STL предыдущего купона не изменены.</p><details><summary>Показать машинный отчёт</summary><pre id="report"></pre></details></section>
<script>
const REPORT=""" + enc(report) + """, MESHES=""" + enc(meshes) + """;
document.getElementById('report').textContent=JSON.stringify(REPORT,null,2);
const c=document.getElementById('view'), ctx=c.getContext('2d'), colors=['#2988aa','#db8540','#64a666','#9d70ba','#cb6586','#a09639','#6684ce','#875946'];
let ax=-0.65,ay=0.55,zoom=1,drag=null;
const all=MESHES.flatMap(m=>m.vertices), lo=[0,1,2].map(k=>all.reduce((a,v)=>Math.min(a,v[k]),Infinity)), hi=[0,1,2].map(k=>all.reduce((a,v)=>Math.max(a,v[k]),-Infinity));
const center=lo.map((x,i)=>(x+hi[i])/2), span=Math.max(...lo.map((x,i)=>hi[i]-x));
function draw(){
 const dpr=window.devicePixelRatio||1,w=c.clientWidth,h=c.clientHeight;c.width=w*dpr;c.height=h*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
 const scale=Math.min(w,h)*0.78/span*zoom, faces=[];
 MESHES.forEach((m,n)=>{const vs=m.vertices.map(v=>{let x=v[0]-center[0],y=v[1]-center[1],z=v[2]-center[2];let xx=x*Math.cos(ay)+z*Math.sin(ay),zz=-x*Math.sin(ay)+z*Math.cos(ay);return [w/2+xx*scale,h/2-(y*Math.cos(ax)-zz*Math.sin(ax))*scale,y*Math.sin(ax)+zz*Math.cos(ax)]});
 m.triangles.forEach(t=>faces.push({v:t.map(i=>vs[i]),color:colors[n%colors.length]}));});
 faces.sort((a,b)=>a.v.reduce((s,v)=>s+v[2],0)-b.v.reduce((s,v)=>s+v[2],0));
 faces.forEach(f=>{ctx.beginPath();f.v.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.closePath();ctx.fillStyle=f.color;ctx.fill();ctx.strokeStyle='#24364d33';ctx.lineWidth=.25;ctx.stroke();});
}
MESHES.forEach((m,i)=>{const item=document.createElement('span'),dot=document.createElement('span');dot.className='dot';dot.style.background=colors[i%colors.length];item.append(dot,document.createTextNode(REPORT.part_names?.[m.id]||m.id));document.getElementById('legend').append(item)});
c.onpointerdown=e=>{drag=[e.clientX,e.clientY];c.setPointerCapture(e.pointerId)};
c.onpointermove=e=>{if(!drag)return;ay+=(e.clientX-drag[0])*.01;ax+=(e.clientY-drag[1])*.01;drag=[e.clientX,e.clientY];draw()};
c.onpointerup=c.onpointercancel=()=>drag=null;
c.onwheel=e=>{e.preventDefault();zoom=Math.max(.3,Math.min(3,zoom*Math.exp(-e.deltaY*.001)));draw()};
document.getElementById('top').onclick=()=>{ax=0;ay=0;zoom=1;draw()};
document.getElementById('iso').onclick=()=>{ax=-.65;ay=.55;zoom=1;draw()};
window.addEventListener('resize',draw);draw();
document.getElementById('save').onclick=()=>{
 const observations={};let valid=true;document.querySelectorAll('[data-measure]').forEach(el=>{const value=el.value===''?null:Number(el.value);if(value!==null&&(!Number.isFinite(value)||value<=0))valid=false;observations[el.dataset.measure]={value,unit:'mm',obtained_by:value===null?'Измерить остывшую деталь по инструкции':null}});
 if(!valid){document.getElementById('feedback').textContent='Проверьте размеры: требуются положительные числа.';return}
 const data={schema:'print-control-observations-v1',kind:'CONTROL_OBSERVATIONS',baseline_digest:REPORT.plan.baseline_digest,trial_artifact_sha256:REPORT.artifacts['PRINT_JOB.3mf'].sha256,cooled:document.getElementById('cooled').checked,job_confirmed:document.getElementById('job-confirmed').checked,instrument:document.getElementById('instrument').value||null,measured_at:document.getElementById('measured-at').value||null,observations,notes:document.getElementById('notes').value,physical_approval:false};
 const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='CONTROL_OBSERVATIONS.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.getElementById('feedback').textContent='Результаты подготовлены к сохранению. Калибровка автоматически не утверждена.';
};
</script></main></html>
"""
