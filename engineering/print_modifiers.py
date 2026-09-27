"""Local Bambu parameter modifiers, with strict geometry/settings round trips."""
from __future__ import annotations
import copy,io,json,math,posixpath,zipfile
import xml.etree.ElementTree as ET
from engineering import print_package as base
from engineering import print_settings as settings_api

NS=base.NS
ET.register_namespace("",NS)
def tag(s): return "{"+NS+"}"+s
def metadata(element):
    result={}
    for m in element.findall("metadata"):
        key=m.get("key")
        if key is None: continue
        if key in result: raise ValueError("Duplicate metadata: "+key)
        result[key]=m.get("value")
    return result

def overrides(element,settings):
    meta=metadata(element)
    result={k:v for k,v in meta.items() if k in settings_api.PART_SETTING_KEYS}
    for k,v in meta.items():
        if k=="extruder" and v!="1": raise ValueError("Unsupported extruder")
        if k in settings and k not in settings_api.PART_SETTING_KEYS and k not in {"name","extruder"}:
            raise ValueError("Unsupported volume setting: "+k)
    return settings_api.validate_part_settings(result)

def project_bytes(parts,settings,title):
    """Modifiers share their parent's build item; never receive their own instance."""
    original=settings_api.project_bytes(parts,settings,title)
    with zipfile.ZipFile(io.BytesIO(original)) as src:
        model=ET.fromstring(src.read("3D/3dmodel.model"));cfg=ET.fromstring(src.read("Metadata/model_settings.config"))
        resources=model.find(tag("resources"))
        next_id=len(parts)+1
        for obj,config,p in zip(list(resources),cfg.findall("object"),parts,strict=True):
            modifiers=p.get("modifiers",[])
            if not modifiers: continue
            if len({m["name"] for m in modifiers})!=len(modifiers):
                raise ValueError("Duplicate modifier name")
            normal=obj.find(tag("mesh"));obj.remove(normal)
            normal_obj=ET.SubElement(resources,tag("object"),id=str(next_id),type="model")
            normal_obj.append(normal)
            components=ET.SubElement(obj,tag("components"))
            ET.SubElement(components,tag("component"),objectid=str(next_id),transform=base.IDENTITY)
            config.find("part").set("id",str(next_id));next_id+=1
            for m in modifiers:
                base.mesh_report(m);settings_api.validate_part_settings(m["print_settings"])
                if not m["print_settings"]: raise ValueError("Empty modifier settings")
                leaf=ET.SubElement(resources,tag("object"),id=str(next_id),type="model")
                mesh=ET.SubElement(leaf,tag("mesh"));vs=ET.SubElement(mesh,tag("vertices"));ts=ET.SubElement(mesh,tag("triangles"))
                for v in m["vertices"]: ET.SubElement(vs,tag("vertex"),**{k:format(x,".12g") for k,x in zip("xyz",v)})
                for t in m["triangles"]: ET.SubElement(ts,tag("triangle"),**{k:str(x) for k,x in zip(("v1","v2","v3"),t)})
                ET.SubElement(components,tag("component"),objectid=str(next_id),transform=base.IDENTITY)
                volume=ET.SubElement(config,"part",id=str(next_id),subtype="modifier_part")
                for k,v in {"name":m["name"],**m["print_settings"]}.items(): ET.SubElement(volume,"metadata",key=k,value=v)
                next_id+=1
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,"w",compression=zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                data=src.read(info.filename)
                if info.filename=="3D/3dmodel.model": data=ET.tostring(model,encoding="utf-8",xml_declaration=True)
                if info.filename=="Metadata/model_settings.config": data=ET.tostring(cfg,encoding="utf-8",xml_declaration=True)
                dst.writestr(info,data)
        return buffer.getvalue()

def inspect_project(path):
    with zipfile.ZipFile(path) as z:
        if len(z.namelist())!=len(set(z.namelist())): raise ValueError("Duplicate ZIP entries")
        def xml(name):
            raw=z.read(name)
            if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw: raise ValueError("Unsafe XML")
            return ET.fromstring(raw)
        def matrix(text):
            a=list(map(float,(text or base.IDENTITY).split()))
            if len(a)!=12 or not all(math.isfinite(v) for v in a): raise ValueError("Invalid transform")
            return a
        def load(name,oid,transforms,stack):
            if (name,oid) in stack: raise ValueError("Cyclic object")
            root=xml(name)
            if root.get("unit")!="millimeter": raise ValueError("Expected mm")
            found=[o for o in root.findall(tag("resources")+"/"+tag("object")) if o.get("id")==oid]
            if len(found)!=1: raise ValueError("Missing/duplicate object")
            obj=found[0];mesh=obj.find(tag("mesh"))
            if mesh is not None:
                vs=[[float(v.get(k)) for k in "xyz"] for v in mesh.findall(tag("vertices")+"/"+tag("vertex"))]
                for a in transforms:
                    vs=[[sum(v[j]*a[j*3+i] for j in range(3))+a[9+i] for i in range(3)] for v in vs]
                ts=[[int(t.get(k)) for k in ("v1","v2","v3")] for t in mesh.findall(tag("triangles")+"/"+tag("triangle"))]
                out=dict(vertices=vs,triangles=ts);base.mesh_report(out)
                return [(oid,out)]
            output=[]
            for c in obj.findall(tag("components")+"/"+tag("component")):
                dest=c.get("{"+base.PNS+"}path",name).lstrip("/")
                if ".." in dest.split("/") or "\\" in dest: raise ValueError("Unsafe model path")
                output+=load(dest,c.get("objectid"),[matrix(c.get("transform"))]+transforms,stack|{(name,oid)})
            if not output: raise ValueError("Empty object")
            return output
        cfg=xml("Metadata/model_settings.config");root=xml("3D/3dmodel.model")
        settings=json.loads(z.read("Metadata/project_settings.config"))
        configs={o.get("id"):o for o in cfg.findall("object")}
        if len(configs)!=len(cfg.findall("object")): raise ValueError("Duplicate config object")
        parts=[]
        for item in root.findall(tag("build")+"/"+tag("item")):
            if item.get("printable","1")!="1": raise ValueError("Nonprintable item")
            config=configs[item.get("objectid")];meta=metadata(config)
            parent_settings=overrides(config,settings)
            leaves=load("3D/3dmodel.model",item.get("objectid"),[matrix(item.get("transform"))],set())
            volumes={v.get("id"):v for v in config.findall("part")}
            if len(volumes)!=len(config.findall("part")) or set(volumes)!={i for i,_ in leaves} or len(leaves)!=len(volumes):
                raise ValueError("Volume inventory mismatch")
            normal=[];mods=[]
            for oid,m in leaves:
                volume=volumes[oid];vmeta=metadata(volume);vs=overrides(volume,settings)
                m["name"]=vmeta.get("name")
                m["print_settings"]=vs
                if volume.get("subtype")=="normal_part":
                    for k,v in vs.items():
                        if k in parent_settings and parent_settings[k]!=v: raise ValueError("Conflicting settings")
                    m["print_settings"]=parent_settings|vs;normal.append(m)
                elif volume.get("subtype")=="modifier_part":
                    if not vs: raise ValueError("Modifier lost its settings")
                    mods.append(m)
                else: raise ValueError("Unsupported volume role")
            if len(normal)!=1: raise ValueError("One physical solid per part required")
            p=normal[0];p["name"]=meta.get("name");p["modifiers"]=mods;parts.append(p)
        if len({p["name"] for p in parts})!=len(parts) or any(not p["name"] for p in parts):
            raise ValueError("Invalid part names")
        return dict(parts=parts,settings=settings,gcodes=[x for x in z.namelist() if x.endswith(".gcode")],
            warnings=z.read("Metadata/slice_info.config").decode() if "Metadata/slice_info.config" in z.namelist() else None)

def compare_projects(expected,actual):
    settings_api.compare_projects(expected,actual)
    actual_parts={p["name"]:p for p in actual["parts"]}
    for p in expected["parts"]:
        before=p.get("modifiers",[]);after=actual_parts[p["name"]].get("modifiers",[])
        if len({m["name"] for m in before})!=len(before) or len({m["name"] for m in after})!=len(after):
            raise ValueError("Duplicate modifier identities")
        if [m["name"] for m in before]!=[m["name"] for m in after]:
            raise ValueError("Modifier inventory changed")
        # The base comparison checks exact triangle inventory and bed placement.
        settings_api.compare_projects(dict(parts=before,settings=expected["settings"]),
                                      dict(parts=after,settings=actual["settings"]))
    return True
