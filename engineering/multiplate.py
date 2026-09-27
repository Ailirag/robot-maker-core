"""Combine configured print plates without altering their local geometry.

Plate origins are Bambu's logical display grid, not new physical placements.
Different machine/global settings are refused; supported process differences
become explicit object overrides. Only native slicing creates toolpaths.
"""
from __future__ import annotations
import copy,hashlib,io,json,math,subprocess,zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
from engineering.print_package import NS, IDENTITY, inspect_project, compare_projects

POLICY=Path(__file__).with_name("bambu_multiplate_policy.json")
RULES=json.loads(POLICY.read_text(encoding="utf-8"))
FILAMENT_KEYS=frozenset(RULES["filament_keys"])
OBJECT_KEYS=frozenset(RULES["object_overrides"])
COMMON_SPEED_KEYS=frozenset(RULES["common_minimum_speed"])
DISPLAY_KEYS=frozenset(("print_settings_id",))
PLATE_ARRAYS=frozenset(("wipe_tower_x","wipe_tower_y"))
SPECIAL_KEYS=frozenset(("flush_volumes_matrix","flush_volumes_vector","filament_self_index","inherits_group","different_settings_to_system","compatible_printers_condition_group","compatible_prints_condition_group"))
SOURCE_MEMBER="Metadata/combined_sources.json"

def sha(raw):return hashlib.sha256(raw).hexdigest()

def metadata(node):
    rows=node.findall("metadata")
    if any(m.get("key") is None and set(m.attrib)!={"face_count"} for m in rows):
        raise ValueError("MULTIPLATE: unsupported unkeyed metadata")
    rows=[m for m in rows if m.get("key") is not None]
    result={m.get("key"):m.get("value") for m in rows}
    if len(result)!=len(rows):raise ValueError("MULTIPLATE: duplicate metadata")
    return result

def put(node,key,value):
    found=node.find("metadata[@key='"+key+"']")
    if found is None:found=ET.SubElement(node,"metadata",key=key)
    found.set("value",str(value))

def xml(raw):
    if b"<!ENTITY" in raw or b"<!DOCTYPE" in raw:raise ValueError("MULTIPLATE: unsafe XML")
    return ET.fromstring(raw)

def bare(node):
    node=copy.deepcopy(node)
    for item in node.iter():
        item.tag=item.tag.removeprefix("{"+NS+"}")
    return node

def serialized(value):
    return ",".join(map(str,value)) if isinstance(value,list) else str(value)

def package(members):
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,raw in sorted(members.items()):
            entry=zipfile.ZipInfo(name,(1980,1,1,0,0,0))
            entry.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(entry,raw)
    return out.getvalue()

def _load(source):
    raw=source["data"]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if len(z.namelist())!=len(set(z.namelist())):raise ValueError("MULTIPLATE: duplicate ZIP member")
        model=xml(z.read("3D/3dmodel.model"));cfg=xml(z.read("Metadata/model_settings.config"))
        settings=json.loads(z.read("Metadata/project_settings.config"))
    if model.get("unit")!="millimeter":raise ValueError("MULTIPLATE: millimeters required")
    if len(cfg.findall("plate"))!=1:raise ValueError("MULTIPLATE: expected one source plate")
    if len(settings.get("filament_type",[]))!=1:raise ValueError("MULTIPLATE: source must use one material")
    if len(settings.get("nozzle_diameter",[]))!=1:raise ValueError("MULTIPLATE: one physical nozzle required")
    if "filament_self_index" in settings or "filament_extruder_variant" in settings:
        variants=settings.get("filament_extruder_variant")
        if (not isinstance(variants,list) or not variants
                or not all(isinstance(v,str) and v for v in variants)
                or settings.get("filament_self_index")!=["1"]*len(variants)):
            raise ValueError("MULTIPLATE: inconsistent filament variant index")
    resources=model.find("{"+NS+"}resources")
    objects=resources.findall("{"+NS+"}object")
    if not objects or len(objects)!=len(resources):raise ValueError("MULTIPLATE: unsupported resources")
    if any(o.find("{"+NS+"}mesh") is None for o in objects):
        raise ValueError("MULTIPLATE: use the configured flat source project")
    if any(p.get("subtype")!="normal_part" for p in cfg.findall("object/part")):
        raise ValueError("MULTIPLATE: unsupported modifier volume")
    return model,cfg,settings

def origin(index,count,settings):
    area=settings.get("printable_area")
    try:p=[list(map(float,s.split("x"))) for s in area]
    except (TypeError,ValueError):raise ValueError("MULTIPLATE: rectangular bed required")
    if len(p)!=4 or p[0]!=[0,0] or p[1][1]!=0 or p[2]!=[p[1][0],p[3][1]] or p[3][0]!=0:
        raise ValueError("MULTIPLATE: rectangular bed required")
    width,height=p[2]
    if not all(math.isfinite(x) and x>0 for x in (width,height)):raise ValueError("MULTIPLATE: invalid bed")
    cols=math.ceil(math.sqrt(count))
    return ((index%cols)*width*1.2,-(index//cols)*height*1.2,0.)

def prepare(sources):
    if not sources or len(sources)>36:raise ValueError("MULTIPLATE: require 1..36 plates")
    ids=[s["id"] for s in sources]
    if len(ids)!=len(set(ids)) or not all(isinstance(s,str) and s for s in ids):
        raise ValueError("MULTIPLATE: duplicate/empty plate ID")
    loaded=[_load(s) for s in sources]
    configs=[r[2] for r in loaded];base=copy.deepcopy(configs[0])
    keys=set().union(*(s.keys() for s in configs))
    differing={k for k in keys if any(s.get(k)!=base.get(k) for s in configs)}
    unsupported=differing-FILAMENT_KEYS-OBJECT_KEYS-DISPLAY_KEYS-PLATE_ARRAYS-SPECIAL_KEYS-COMMON_SPEED_KEYS
    if unsupported:raise ValueError("MULTIPLATE: incompatible global settings: "+", ".join(sorted(unsupported)))
    for key in COMMON_SPEED_KEYS & keys:
        values=[s.get(key) for s in configs]
        if not all(isinstance(v,list) and len(v)==len(values[0]) for v in values):
            raise ValueError("MULTIPLATE: incompatible common speed "+key)
        for value in values:
            if any(not math.isfinite(float(v)) or float(v)<=0 for v in value):
                raise ValueError("MULTIPLATE: explicit positive common speed required")
        base[key]=[format(min(float(v[i]) for v in values),".12g") for i in range(len(values[0]))]
    variants=[];slots=[]
    for settings in configs:
        variant={k:settings[k] for k in sorted(FILAMENT_KEYS & settings.keys())}
        if variant not in variants:variants.append(variant)
        slots.append(variants.index(variant)+1)
    for key in FILAMENT_KEYS & keys:
        values=[s.get(key) for s in variants]
        if any(v is None for v in values):raise ValueError("MULTIPLATE: incomplete filament setting "+key)
        if all(isinstance(v,list) for v in values):
            base[key]=[x for v in values for x in v]
        elif any(v!=values[0] for v in values):raise ValueError("MULTIPLATE: scalar filament conflict "+key)
    representatives=[configs[slots.index(i+1)] for i in range(len(variants))]
    if "filament_self_index" in keys:
        base["filament_self_index"]=[str(i+1) for i,s in enumerate(representatives)
            for _ in s["filament_extruder_variant"]]
    for key in ("inherits_group","different_settings_to_system","compatible_printers_condition_group"):
        if key not in keys:continue
        if any(not isinstance(s.get(key),list) or len(s[key])!=3 for s in configs):
            raise ValueError("MULTIPLATE: malformed source profile group "+key)
        base[key]=[configs[0][key][0]]+[s[key][1] for s in representatives]+[configs[0][key][2]]
    key="compatible_prints_condition_group"
    if key in keys:
        if any(not isinstance(s.get(key),list) or len(s[key])!=1 for s in configs):
            raise ValueError("MULTIPLATE: malformed source profile group "+key)
        base[key]=[s[key][0] for s in representatives]
    for key in PLATE_ARRAYS & keys:
        if any(not isinstance(s[key],list) or len(s[key])!=1 for s in configs):
            raise ValueError("MULTIPLATE: expected single plate array "+key)
        base[key]=[s[key][0] for s in configs]
    if "flush_volumes_matrix" in keys:
        if any(s["flush_volumes_matrix"]!=["0"] for s in configs):
            raise ValueError("MULTIPLATE: unsupported flush matrix")
        base["flush_volumes_matrix"]=["0"]*(len(variants)**2)
    if "flush_volumes_vector" in keys:
        if any(len(s["flush_volumes_vector"])!=2 for s in configs):
            raise ValueError("MULTIPLATE: unsupported flush vector")
        representatives=[configs[slots.index(i+1)] for i in range(len(variants))]
        base["flush_volumes_vector"]=[v for s in representatives for v in s["flush_volumes_vector"]]
    base["print_settings_id"]="Combined plates - process settings on objects"
    model=ET.Element("model",xmlns=NS,unit="millimeter")
    model.set("xmlns:BambuStudio","http://schemas.bambulab.com/package/2021")
    for m in loaded[0][0].findall("{"+NS+"}metadata"):
        model.append(bare(m))
    resources=ET.SubElement(model,"resources");build=ET.SubElement(model,"build")
    cfg=ET.Element("config");plates=[];recipes=[];seen_names=set();next_id=1
    for i,(source,(src,sc,settings),slot) in enumerate(zip(sources,loaded,slots)):
        objs=src.findall("{"+NS+"}resources/{"+NS+"}object")
        mapping={o.get("id"):str(next_id+j) for j,o in enumerate(objs)}
        if len(mapping)!=len(objs):raise ValueError("MULTIPLATE: duplicate source object")
        next_id+=len(objs)
        for obj in objs:
            obj=bare(obj);obj.set("id",mapping[obj.get("id")]);resources.append(obj)
        dx,dy,_=origin(i,len(sources),base)
        for item in src.findall("{"+NS+"}build/{"+NS+"}item"):
            item=bare(item);item.set("objectid",mapping[item.get("objectid")])
            tr=list(map(float,item.get("transform",IDENTITY).split()))
            if len(tr)!=12 or not all(math.isfinite(x) for x in tr):raise ValueError("MULTIPLATE: invalid transform")
            tr[9]+=dx;tr[10]+=dy;item.set("transform"," ".join(format(v,".12g") for v in tr))
            build.append(item)
        for obj in sc.findall("object"):
            obj=copy.deepcopy(obj);obj.set("id",mapping[obj.get("id")])
            name=metadata(obj).get("name")
            if not name or name in seen_names:raise ValueError("MULTIPLATE: duplicate/unnamed part")
            seen_names.add(name)
            for key in sorted(differing&OBJECT_KEYS):
                if key not in metadata(obj):put(obj,key,serialized(settings[key]))
            put(obj,"extruder",slot)
            for part in obj.findall("part"):
                part.set("id",mapping[part.get("id")])
                if part.find("metadata[@key='extruder']") is not None:put(part,"extruder",slot)
            cfg.append(obj)
        plate=copy.deepcopy(sc.find("plate"))
        for m in list(plate.findall("metadata")):
            if m.get("key") in {"gcode_file","thumbnail_file","thumbnail_no_light_file","top_file","pick_file"}:plate.remove(m)
        put(plate,"plater_id",i+1);put(plate,"plater_name",source["id"]+" · "+source.get("title",source["id"]))
        put(plate,"filament_maps"," ".join("1" for _ in variants))
        put(plate,"filament_volume_maps"," ".join("0" for _ in variants))
        for inst in plate.findall("model_instance"):
            md=metadata(inst)
            put(inst,"object_id",mapping[md["object_id"]])
            put(inst,"identify_id",mapping[md["object_id"]])
        plates.append(plate)
        recipes.append(dict(id=source["id"],sha256=sha(source["data"]),slot=slot,origin_mm=[dx,dy,0.]))
    cfg.extend(plates)
    return package({
        "3D/3dmodel.model":ET.tostring(model,encoding="utf-8",xml_declaration=True),
        "Metadata/model_settings.config":ET.tostring(cfg,encoding="utf-8",xml_declaration=True),
        "Metadata/project_settings.config":json.dumps(base,ensure_ascii=False,sort_keys=True).encode(),
        SOURCE_MEMBER:json.dumps(dict(schema="combined-print-sources-v1",plates=recipes),sort_keys=True).encode(),
        "[Content_Types].xml":b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="config" ContentType="application/octet-stream"/><Default Extension="json" ContentType="application/json"/></Types>',
        "_rels/.rels":b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'})

def inspect(path):
    whole=inspect_project(path)
    with zipfile.ZipFile(path) as z:
        cfg=xml(z.read("Metadata/model_settings.config"))
    objects={o.get("id"):metadata(o) for o in cfg.findall("object")}
    volume_settings={}
    for obj in cfg.findall("object"):
        rows=[]
        for part in obj.findall("part"):
            if part.get("subtype")!="normal_part":
                raise ValueError("MULTIPLATE: unexpected native volume type")
            values=metadata(part)
            rows.append({key:value for key,value in values.items()
                         if key!="matrix" and not key.startswith("source_")})
        volume_settings[obj.get("id")]=rows
    meshes={p["name"]:p for p in whole["parts"]}
    if len(meshes)!=len(whole["parts"]):raise ValueError("MULTIPLATE: duplicate part")
    plates=[];used=[];nodes=cfg.findall("plate")
    for i,p in enumerate(nodes):
        md=metadata(p)
        if md.get("plater_id")!=str(i+1):raise ValueError("MULTIPLATE: incorrect plate index")
        names=[];settings={};volumes={};parts=[];offset=origin(i,len(nodes),whole["settings"])
        for inst in p.findall("model_instance"):
            im=metadata(inst);oid=im["object_id"];used.append(oid)
            if im.get("instance_id")!="0" or oid not in objects:raise ValueError("MULTIPLATE: bad plate assignment")
            obj=objects[oid];name=obj["name"];names.append(name);settings[name]=obj;volumes[name]=volume_settings[oid]
            part=copy.deepcopy(meshes[name])
            part["vertices"]=[[v[k]-offset[k] for k in range(3)] for v in part["vertices"]];parts.append(part)
        plates.append(dict(index=i+1,title=md.get("plater_name"),names=names,objects=settings,volumes=volumes,parts=parts))
    if len(used)!=len(set(used)) or set(used)!=set(objects):raise ValueError("MULTIPLATE: missing/duplicate plate object")
    return dict(plates=plates,settings=whole["settings"],gcodes=whole["gcodes"])

def verify_sources(path,sources):
    return verify_prepared(path,prepare(sources))


def verify_prepared(path,prepared):
    expected=inspect(io.BytesIO(prepared));actual=inspect(path)
    if len(actual["plates"])!=len(expected["plates"]):raise ValueError("MULTIPLATE: plate count changed")
    for key,value in expected["settings"].items():
        if key in DISPLAY_KEYS:continue
        if actual["settings"].get(key)!=value:raise ValueError("MULTIPLATE: global setting changed: "+key)
    count=0
    for ep,ap in zip(expected["plates"],actual["plates"]):
        if ep["names"]!=ap["names"]:raise ValueError("MULTIPLATE: plate composition changed")
        if ep["objects"]!=ap["objects"]:raise ValueError("MULTIPLATE: object settings changed")
        if ep["volumes"]!=ap["volumes"]:raise ValueError("MULTIPLATE: volume settings changed")
        compare_projects(dict(parts=ep["parts"],settings={}),dict(parts=ap["parts"],settings={}))
        count+=len(ap["parts"])
    return dict(plates=len(expected["plates"]),parts=count,local_geometry_preserved=True,supported_settings_preserved=True,
                common_minimum_speeds={k:actual["settings"][k] for k in COMMON_SPEED_KEYS & actual["settings"].keys()})

def validate_native_receipt(path,count):
    r=path if isinstance(path,dict) else json.loads(Path(path).read_text(encoding="utf-8"))
    plates=r.get("sliced_plates",[])
    if r.get("return_code")!=0 or r.get("error_string")!="Success.":
        raise ValueError("MULTIPLATE: native slicer failed")
    if [p.get("id") for p in plates]!=list(range(1,count+1)) or any(p.get("warning_message")!="" for p in plates):
        raise ValueError("MULTIPLATE: native plate count or warnings")
    return r

def sources_from_contracts(root,contracts):
    """Bind layout to the final native settings, including version migrations."""
    from engineering.delivery import read_bound
    result=[]
    for contract in contracts:
        for job in contract["jobs"]:
            prepared=read_bound(root,job["project"])
            sliced=read_bound(root,job["sliced_project"])
            with zipfile.ZipFile(io.BytesIO(prepared)) as z:
                members={n:z.read(n) for n in z.namelist()}
            with zipfile.ZipFile(io.BytesIO(sliced)) as z:
                members["Metadata/project_settings.config"]=z.read("Metadata/project_settings.config")
            result.append(dict(id=job["id"],title=job["title"],data=package(members),
                               inputs=[job["project"],job["sliced_project"]]))
    return result
