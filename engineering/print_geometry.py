"""Apply one calibration in build123d, arrange afterwards, export verified trials."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile

from build123d import Compound, Pos, export_step, import_step
from engineering.print_calibration import analyse, digest, inspect_project, number, NS
from engineering.instructions.calibration_guide import render


def _bounds(shape):
    b = shape.bounding_box()
    return [[float(x) for x in b.min], [float(x) for x in b.max]]


def _size(shape):
    b = _bounds(shape)
    return [b[1][i]-b[0][i] for i in range(3)]


def _mesh(shape):
    vertices, triangles = shape.tessellate(0.03, 0.08)
    return {"vertices":[[float(c) for c in v] for v in vertices],
            "triangles":[list(t) for t in triangles]}



def volume(shape):
    """Adaptive surface integration: the default CAD mass API is inaccurate on scaled splines."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    if not shape:
        return 0.0
    properties = GProp_GProps()
    error = BRepGProp.VolumePropertiesGK_s(shape.wrapped, properties, 1e-10, True, True)
    result = properties.Mass()
    if error < 0 or error > 1e-6 or not __import__("math").isfinite(result):
        raise ValueError("BREP: adaptive volume integration failed")
    return result

def _fingerprint(parts):
    return digest({key:{"volume":volume(shape),"mesh":_mesh(shape)}
                   for key,shape in sorted(parts.items())})


def _valid(shape):
    if not shape.is_valid or len(shape.solids()) != 1 or volume(shape) <= 0:
        raise ValueError("BREP: valid single solid required")


def prepare(parts, *, recipe, facts, project, materials, purpose):
    if "compensation_applied" in parts:
        raise ValueError("DOUBLE_COMPENSATION: batch has already been transformed")
    if purpose not in ("trial", "production"):
        raise ValueError("PURPOSE: trial or production required")
    plan = analyse(recipe, facts, project)
    if purpose == "production" and plan["status"] != "CONTROL_VERIFIED":
        raise ValueError("CONTROL_REQUIRED: complete successful control record required")
    if not parts or set(parts) != set(materials):
        raise ValueError("MATERIAL: declare the material of every printed part")
    if any(materials[k] != plan["material"]["type"] for k in parts):
        raise ValueError("MATERIAL: split printed materials into separate calibrated batches")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", k) for k in parts):
        raise ValueError("PART: safe unique part IDs required")
    factors = plan["factors"]
    scaled = {}
    for key,shape in parts.items():
        _valid(shape)
        result = copy.deepcopy(shape).scale(tuple(factors), about=(0,0,0))
        _valid(result)
        expected_volume = volume(shape)*factors[0]*factors[1]*factors[2]
        if abs(volume(result)-expected_volume) > max(1e-5,expected_volume*1e-7):
            raise ValueError("BREP: compensated volume differs")
        if any(abs(a-b*f) > 1e-5 for a,b,f in zip(_size(result),_size(shape),factors)):
            raise ValueError("BREP: compensated dimensions differ")
        result.label = key
        scaled[key] = result
    return {"compensation_applied":plan["digest"], "plan":plan, "purpose":purpose,
            "parts":scaled, "nominal":copy.deepcopy(parts), "materials":dict(materials),
            "fingerprint":_fingerprint(scaled), "nominal_fingerprint":_fingerprint(parts)}


def arrange(batch, *, bed_mm, gap_mm, margin_mm):
    if len(bed_mm) != 3:
        raise ValueError("BED: width, depth and height required")
    bed = [number(v,positive=True) for v in bed_mm]
    gap, margin = number(gap_mm,positive=True), number(margin_mm,positive=True)
    if _fingerprint(batch["parts"]) != batch["fingerprint"]:
        raise ValueError("STALE: prepared geometry changed")
    placed, placements = {}, {}
    x = y = margin
    row_height = 0.0
    for key,shape in batch["parts"].items():
        w,d,h = _size(shape)
        if w+2*margin > bed[0] or d+2*margin > bed[1] or h > bed[2]:
            raise ValueError("BED: compensated part does not fit")
        if x+w+margin > bed[0]:
            x = margin
            y += row_height+gap
            row_height = 0.0
        if y+d+margin > bed[1]:
            raise ValueError("BED: compensated job does not fit")
        lo = _bounds(shape)[0]
        translation = [x-lo[0],y-lo[1],-lo[2]]
        placed[key] = copy.deepcopy(shape).moved(Pos(*translation))
        placements[key] = translation
        x += w+gap
        row_height = max(row_height,d)
    job = {**batch,"placed":placed,"placements":placements,"bed_mm":bed,
           "gap_mm":gap,"margin_mm":margin,"placed_fingerprint":_fingerprint(placed)}
    validate_job(job)
    return job


def validate_job(job):
    if (_fingerprint(job["parts"]) != job["fingerprint"] or
        _fingerprint(job["nominal"]) != job["nominal_fingerprint"] or
        _fingerprint(job["placed"]) != job["placed_fingerprint"]):
        raise ValueError("STALE: geometry/layout changed after validation")
    bed, margin, gap = job["bed_mm"],job["margin_mm"],job["gap_mm"]
    items = list(job["placed"].items())
    for key,shape in items:
        _valid(shape)
        lo,hi = _bounds(shape)
        if any(lo[i] < margin-1e-5 or hi[i] > bed[i]-margin+1e-5 for i in (0,1)) or abs(lo[2]) > 1e-5 or hi[2] > bed[2]+1e-5:
            raise ValueError("BED: placement outside declared printable envelope")
        expected = copy.deepcopy(job["parts"][key]).moved(Pos(*job["placements"][key]))
        if digest(_mesh(expected)) != digest(_mesh(shape)):
            raise ValueError("STALE: placement does not match transformed part")
    for i,(_,a) in enumerate(items):
        ba=_bounds(a)
        for _,b in items[i+1:]:
            bb=_bounds(b)
            separation=max(bb[0][0]-ba[1][0],ba[0][0]-bb[1][0],
                           bb[0][1]-ba[1][1],ba[0][1]-bb[1][1])
            if separation < gap-1e-5:
                raise ValueError("COLLISION: bounding boxes lack declared gap")
            common=a & b
            if common is not None and volume(common) > 1e-6:
                raise ValueError("COLLISION: positive B-Rep interference")
    return {"valid_brep":True,"pair_collision_count":0,"fits_bed":True,
            "bed_mm":bed,"gap_mm":gap,"margin_mm":margin,
            "bounds_mm":_bounds(Compound([copy.deepcopy(s) for _,s in items]))}


def _stl(mesh):
    data=bytearray(b"calibrated print mesh".ljust(80,b"\0"))
    data.extend(struct.pack("<I",len(mesh["triangles"])))
    for tri in mesh["triangles"]:
        points=[mesh["vertices"][i] for i in tri]
        a,b,c=points
        u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
        normal=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        length=sum(x*x for x in normal)**.5
        normal=[x/length for x in normal] if length else [0,0,0]
        data.extend(struct.pack("<12fH",*normal,*a,*b,*c,0))
    return bytes(data)


def _project_bytes(mesh, settings, plan):
    vertices="".join('<vertex x="%.12g" y="%.12g" z="%.12g"/>'%tuple(v) for v in mesh["vertices"])
    triangles="".join('<triangle v1="%d" v2="%d" v3="%d"/>'%tuple(t) for t in mesh["triangles"])
    model=f'''<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="{NS["c"]}" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" unit="millimeter">
<metadata name="Application">Calibration generator</metadata><metadata name="BambuStudio:3mfVersion">1</metadata>
<resources><object id="1" type="model"><mesh><vertices>{vertices}</vertices><triangles>{triangles}</triangles></mesh></object></resources>
<build><item objectid="1" printable="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/></build></model>'''
    config='''<?xml version="1.0" encoding="UTF-8"?><config><object id="1">
<metadata key="name" value="CALIBRATION_TRIAL"/><metadata key="extruder" value="1"/>
<part id="1" subtype="normal_part"><metadata key="name" value="PRINT_JOB.stl"/><metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/></part></object>
<plate><metadata key="plater_id" value="1"/><metadata key="plater_name" value="CALIBRATION_TRIAL"/>
<model_instance><metadata key="object_id" value="1"/><metadata key="instance_id" value="0"/><metadata key="identify_id" value="1"/></model_instance></plate></config>'''
    members={"3D/3dmodel.model":model,"Metadata/model_settings.config":config,
             "Metadata/project_settings.config":json.dumps(settings,ensure_ascii=False,sort_keys=True),
             "Metadata/calibration.json":json.dumps({"schema":"print-compensation-applied-v1",
                 "baseline_digest":plan["baseline_digest"],"factors":plan["factors"],"physical_approval":False},sort_keys=True),
             "[Content_Types].xml":'''<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="config" ContentType="text/xml"/><Default Extension="json" ContentType="application/json"/></Types>''',
             "_rels/.rels":'''<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'''}
    import io
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,content in sorted(members.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,content.encode())
    return stream.getvalue()



def _svg_view(meshes, view, title):
    """Orthographic engineering view of the same exported triangles, with depth sorting."""
    from html import escape
    colors = ["#2988aa","#db8540","#64a666","#9d70ba","#cb6586","#a09639","#6684ce"]
    def project(v):
        x,y,z = v
        if view=="top": return (x,y,z)
        if view=="bottom": return (-x,y,-z)
        if view=="side": return (x,z,-y)
        return (0.866*x-0.5*y,0.25*x+0.433*y+0.866*z, -0.433*x-0.75*y+0.5*z)
    shapes = [(m,[project(v) for v in m["vertices"]]) for m in meshes]
    points = [v for _,vs in shapes for v in vs]
    lo = [min(v[k] for v in points) for k in (0,1)]
    hi = [max(v[k] for v in points) for k in (0,1)]
    scale = min(860/max(hi[0]-lo[0],1),490/max(hi[1]-lo[1],1))
    def screen(v):
        return (480+(v[0]-(lo[0]+hi[0])/2)*scale,320-(v[1]-(lo[1]+hi[1])/2)*scale)
    faces=[]
    for n,(m,vs) in enumerate(shapes):
        for tri in m["triangles"]:
            vertices=[vs[i] for i in tri]
            faces.append((sum(v[2] for v in vertices),vertices,colors[n%len(colors)]))
    content=[]
    for _,vs,color in sorted(faces,key=lambda item:item[0]):
        pts=" ".join("%.3f,%.3f"%screen(v) for v in vs)
        content.append('<polygon points="'+pts+'" fill="'+color+'" stroke="#263b4d" stroke-width="0.15"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 620" role="img" aria-label="'+escape(title,quote=True)+'">'
            '<rect width="960" height="620" fill="#f6f9fc"/><text x="24" y="32" font-family="sans-serif" font-size="20">'+escape(title)+'</text>'+
            "".join(content)+'<text x="24" y="600" font-family="sans-serif" font-size="14">Ортографическая проекция · '+escape(view)+' · геометрия для проверки, не чертёж 1:1</text></svg>')

def export_job(job, destination, *, recipe, facts, project, title, sources=None,
               control_targets=None, measurement_notes=None, interface_checks=None, part_names=None, sections=None, input_manifest=None):
    destination=Path(destination)
    if destination.exists():
        raise ValueError("DESTINATION: output must be a new immutable directory")
    # Re-read the actual project: callers cannot reuse an inspection after file changes.
    current_project=inspect_project(project["path"],expected_sha256=project["sha256"])
    fresh=analyse(recipe,facts,current_project)
    if fresh["digest"] != job["plan"]["digest"]:
        raise ValueError("STALE: calibration inputs changed after layout")
    checks=validate_job(job)
    if job["bed_mm"] != current_project["bed_mm"]:
        raise ValueError("BED: layout differs from saved printer bed")
    destination.mkdir(parents=True)
    compound=Compound(list(job["placed"].values()))
    meshes=[dict(id=key,**_mesh(shape)) for key,shape in job["placed"].items()]
    mesh=_mesh(compound)
    (destination/"PRINT_JOB.stl").write_bytes(_stl(mesh))
    export_step(compound,destination/"PRINT_JOB.step")
    reopened=import_step(destination/"PRINT_JOB.step")
    if (not reopened.is_valid or len(reopened.solids()) != len(job["parts"]) or
        abs(volume(reopened)-volume(compound))>max(1e-4,volume(compound)*1e-6)):
        raise ValueError("ARTIFACT: exported STEP differs from print geometry")
    (destination/"PRINT_JOB.3mf").write_bytes(_project_bytes(mesh,current_project["settings"],fresh))
    artifact_project=inspect_project(destination/"PRINT_JOB.3mf")
    if artifact_project["settings"] != current_project["settings"] or artifact_project["model_scale_xyz"] != [1,1,1]:
        raise ValueError("ARTIFACT: project settings or transform changed")
    parts=[]
    for key,shape in job["parts"].items():
        export_step(shape,destination/(key+".step"))
        (destination/(key+".stl")).write_bytes(_stl(_mesh(shape)))
        parts.append({"id":key,"nominal_size_mm":[round(x,6) for x in _size(job["nominal"][key])],
                      "print_size_mm":[round(x,6) for x in _size(shape)],"volume_mm3":volume(shape),
                      "nominal_volume_mm3":volume(job["nominal"][key]),
                      "translation_mm":job["placements"][key]})
    def receipt(path):
        raw=path.read_bytes()
        return {"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)}


    pictures = {}
    for name in ("top","bottom","side"):
        data=_svg_view(meshes,name,title+" · "+name).encode()
        (destination/(name+".svg")).write_bytes(data)
        pictures[name+".svg"]=data
    for name,shape in (sections or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            raise ValueError("PART: safe section ID required")
        _valid(shape)
        data=_svg_view([dict(id=name,**_mesh(shape))],"iso","Разрез / "+name).encode()
        (destination/(name+".svg")).write_bytes(data)
        pictures[name+".svg"]=data
        export_step(shape,destination/(name+".step"))
    artifacts={p.name:receipt(p) for p in sorted(destination.iterdir())}
    from importlib import metadata
    import platform
    runtime={"python":platform.python_version(),"packages":{name:metadata.version(name) for name in ("build123d","cadquery-ocp-novtk")}}
    settings=current_project["settings"]
    report={"schema":"calibrated-print-bundle-v1","title":title,"purpose":job["purpose"],
            "plan":fresh,"parts":parts,"checks":checks,"artifacts":artifacts,
            "sources":sources or {},"physical_approval":False,
            "runtime":runtime,"total_volume_mm3":volume(compound),"solid_count":len(job["parts"]),
            "project_summary":{"Принтер":current_project["printer"],"Профиль":settings.get("print_settings_id"),
                "Филамент":current_project["material_preset"],"Слой, мм":settings.get("layer_height"),
                "Стол, мм":current_project["bed_mm"],"Масштаб модели / Shrinkage":"100% / 100%",
                "XY отверстия / контур":"0 / 0 мм","Первый слой, компенсация":settings.get("elefant_foot_compensation"),
                "Сопло, первый / остальные":str(settings.get("nozzle_temperature_initial_layer"))+" / "+str(settings.get("nozzle_temperature")),
                "Пластина":settings.get("curr_bed_type"),
                "Стол, первый / остальные":str(settings.get("textured_plate_temp_initial_layer"))+" / "+str(settings.get("textured_plate_temp"))}}
    report["input_manifest"] = input_manifest or {}
    report["interface_checks"] = interface_checks or {}
    report["part_names"] = part_names or {}
    if control_targets is not None: report["control_targets"]=control_targets
    if measurement_notes is not None: report["measurement_notes"]=measurement_notes
    (destination/"REPORT.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    downloads={name:(destination/name).read_bytes() for name in artifacts if name.endswith((".3mf",".stl"))}
    page=render(report,meshes,downloads,pictures=pictures)
    (destination/"INSTRUCTION.html").write_text(page,encoding="utf-8",newline="\n")
    all_files={p.name:receipt(p) for p in sorted(destination.iterdir())}
    manifest={"schema":"calibrated-print-manifest-v1","artifacts":all_files,
              "plan_digest":fresh["digest"],"source_project_sha256":project["sha256"],"physical_approval":False}
    (destination/"MANIFEST.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    return report


def verify_bundle(directory):
    directory=Path(directory)
    problems=[]
    try:
        manifest=json.loads((directory/"MANIFEST.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != "calibrated-print-manifest-v1" or manifest.get("physical_approval") is not False:
            raise ValueError("manifest schema/physical flag")
        expected=set(manifest["artifacts"])|{"MANIFEST.json"}
        if {p.name for p in directory.iterdir()} != expected:
            problems.append("ARTIFACT_SET: missing or unexpected files")
        for name,record in manifest["artifacts"].items():
            if Path(name).name != name:
                raise ValueError("unsafe artifact path")
            data=(directory/name).read_bytes()
            if len(data)!=record["bytes"] or hashlib.sha256(data).hexdigest()!=record["sha256"]:
                problems.append("HASH: "+name)
        report=json.loads((directory/"REPORT.json").read_text(encoding="utf-8"))
        plan=report["plan"]
        if digest({k:v for k,v in plan.items() if k!="digest"})!=plan["digest"] or plan["digest"]!=manifest["plan_digest"]:
            problems.append("PLAN_HASH: inconsistent calibration")
        if report["physical_approval"] is not False:
            problems.append("PHYSICAL: bundle cannot grant approval")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        problems.append("BUNDLE: "+str(exc))
    return problems
