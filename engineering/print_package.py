"""Verified multipart print projects and real slicer receipts; no physical authority."""
from __future__ import annotations
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zipfile

NS="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PNS="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
IDENTITY="1 0 0 0 1 0 0 0 1 0 0 0"

def digest(data):
    return hashlib.sha256(data).hexdigest()

def stl_mesh(raw):
    if len(raw)<84: raise ValueError("STL: truncated")
    n=struct.unpack_from("<I",raw,80)[0]
    if len(raw)!=84+50*n: raise ValueError("STL: invalid length")
    index={}; vertices=[]; triangles=[]
    for row in struct.iter_unpack("<12fH",raw[84:]):
        face=[]
        for j in (3,6,9):
            v=tuple(row[j:j+3])
            if v not in index: index[v]=len(vertices); vertices.append(list(v))
            face.append(index[v])
        triangles.append(face)
    result=dict(vertices=vertices,triangles=triangles)
    mesh_report(result)
    return result

def mesh_report(mesh):
    vs,ts=mesh["vertices"],mesh["triangles"]
    if not vs or not ts: raise ValueError("MESH: empty")
    if any(len(v)!=3 or any(isinstance(x,bool) or not math.isfinite(x) for x in v) for v in vs):
        raise ValueError("MESH: invalid vertex")
    edges=Counter(); directed=Counter(); total=0.0
    adjacent={i:set() for i in range(len(vs))}
    for t in ts:
        if len(t)!=3 or any(type(i) is not int or i<0 or i>=len(vs) for i in t) or len(set(t))!=3:
            raise ValueError("MESH: invalid triangle")
        a,b,c=(vs[i] for i in t)
        u=[b[i]-a[i] for i in range(3)]; v=[c[i]-a[i] for i in range(3)]
        cross=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        if sum(x*x for x in cross)<1e-20: raise ValueError("MESH: degenerate")
        total+=sum(a[i]*cross[i] for i in range(3))/6
        for l,r in zip(t,t[1:]+t[:1]):
            edges[tuple(sorted((l,r)))]+=1; directed[(l,r)]+=1
            adjacent[l].add(r); adjacent[r].add(l)
    if any(v!=2 for v in edges.values()) or any(directed[(b,a)]!=n for (a,b),n in directed.items()):
        raise ValueError("MESH: open/nonmanifold/inconsistent winding")
    unseen=set(range(len(vs))); count=0
    while unseen:
        todo=[unseen.pop()];count+=1
        while todo:
            for n in adjacent[todo.pop()] & unseen: unseen.remove(n);todo.append(n)
    if total<=0: raise ValueError("MESH: nonpositive volume")
    bounds=[[min(v[k] for v in vs) for k in range(3)],[max(v[k] for v in vs) for k in range(3)]]
    return dict(vertices=len(vs),triangles=len(ts),components=count,volume_mm3=total,bounds_mm=bounds)

def check_bed(parts,settings):
    area=settings.get("printable_area")
    if settings.get("bed_exclude_area") not in (None,[]): raise ValueError("BED: unsupported excluded areas")
    try: points=[list(map(float,s.split("x"))) for s in area]
    except (TypeError,ValueError): raise ValueError("BED: invalid")
    if len(points)!=4 or points[0]!=[0,0] or points[1][1]!=0 or points[2]!=[points[1][0],points[3][1]] or points[3][0]!=0:
        raise ValueError("BED: rectangular positive bed required")
    width,height=points[2]
    if min(width,height)<=0: raise ValueError("BED: positive bed required")
    if settings.get("brim_type") not in ("outer_only","no_brim"): raise ValueError("BRIM: explicit width required")
    brim=float(settings["brim_width"]) if settings["brim_type"]!="no_brim" else 0.0
    if not math.isfinite(brim) or brim<0: raise ValueError("BRIM: invalid")
    for p in parts:
        r=mesh_report(p)
        if r["components"]!=1: raise ValueError("PART: each named part must be one connected solid")
        lo,hi=r["bounds_mm"]
        if abs(lo[2])>1e-4 or hi[2]>float(settings["printable_height"])+1e-4:
            raise ValueError("BED: part not on bed or too high")
        if lo[0]-brim<-.0001 or lo[1]-brim<-.0001 or hi[0]+brim>width+.0001 or hi[1]+brim>height+.0001:
            raise ValueError("BED: brim exceeds printable area")
    return True

def project_bytes(parts,settings,title):
    names=[p["name"] for p in parts]
    if len(names)!=len(set(names)) or not names: raise ValueError("PART: duplicate/empty list")
    check_bed(parts,settings)
    model=ET.Element("model",xmlns=NS,unit="millimeter")
    # Bambu uses Application as its project-format discriminator, not just attribution.
    ET.SubElement(model,"metadata",name="Application").text="BambuStudio-02.08.02.60"
    ET.SubElement(model,"metadata",name="Generator").text="engineering.print_package; not saved by the GUI"
    ET.SubElement(model,"metadata",name="BambuStudio:3mfVersion").text="1"
    model.set("xmlns:BambuStudio","http://schemas.bambulab.com/package/2021")
    resources=ET.SubElement(model,"resources"); build=ET.SubElement(model,"build")
    cfg=ET.Element("config");plate=ET.SubElement(cfg,"plate")
    for k,v in [("plater_id","1"),("plater_name",title),("locked","false"),("filament_map_mode","Auto For Flush"),("filament_maps","1"),("filament_volume_maps","0")]:
        ET.SubElement(plate,"metadata",key=k,value=v)
    for i,p in enumerate(parts,1):
        obj=ET.SubElement(resources,"object",id=str(i),type="model",name=p["name"])
        mesh=ET.SubElement(obj,"mesh"); vs=ET.SubElement(mesh,"vertices");ts=ET.SubElement(mesh,"triangles")
        for v in p["vertices"]: ET.SubElement(vs,"vertex",**{k:format(x,".12g") for k,x in zip("xyz",v)})
        for t in p["triangles"]: ET.SubElement(ts,"triangle",**{k:str(x) for k,x in zip(("v1","v2","v3"),t)})
        ET.SubElement(build,"item",objectid=str(i),printable="1",transform=IDENTITY)
        config=ET.SubElement(cfg,"object",id=str(i))
        for k,v in [("name",p["name"]),("extruder","1")]: ET.SubElement(config,"metadata",key=k,value=v)
        part=ET.SubElement(config,"part",id=str(i),subtype="normal_part")
        ET.SubElement(part,"metadata",key="name",value=p["name"])
        instance=ET.SubElement(plate,"model_instance")
        for k,v in [("object_id",str(i)),("instance_id","0"),("identify_id",str(i))]:
            ET.SubElement(instance,"metadata",key=k,value=v)
    cfg.remove(plate);cfg.append(plate)
    members={"3D/3dmodel.model":ET.tostring(model,encoding="utf-8",xml_declaration=True),
             "Metadata/model_settings.config":ET.tostring(cfg,encoding="utf-8",xml_declaration=True),
             "Metadata/project_settings.config":json.dumps(settings,ensure_ascii=False,sort_keys=True).encode(),
             "[Content_Types].xml":b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="config" ContentType="application/octet-stream"/></Types>',
             "_rels/.rels":b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'}
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,data in sorted(members.items()):
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,data)
    return stream.getvalue()

def inspect_project(path):
    """Read our projects and the slicer's external component layout, in bed coordinates."""
    with zipfile.ZipFile(path) as z:
        if len(z.namelist())!=len(set(z.namelist())): raise ValueError("ZIP: duplicate entries")
        def xml(name):
            raw=z.read(name)
            if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw: raise ValueError("XML: unsafe")
            root=ET.fromstring(raw)
            return root
        def matrix(text):
            a=list(map(float,(text or IDENTITY).split()))
            if len(a)!=12 or not all(math.isfinite(x) for x in a): raise ValueError("TRANSFORM: invalid")
            return a
        def apply(v,a): return [sum(v[j]*a[j*3+i] for j in range(3))+a[9+i] for i in range(3)]
        def load(name,oid,transforms,stack):
            key=(name,oid)
            if key in stack: raise ValueError("MODEL: cyclic reference")
            root=xml(name)
            if root.get("unit")!="millimeter": raise ValueError("UNIT: expected millimeters")
            found=[o for o in root.findall("{"+NS+"}resources/{"+NS+"}object") if o.get("id")==oid]
            if len(found)!=1: raise ValueError("MODEL: missing/duplicate object")
            obj=found[0]; mesh=obj.find("{"+NS+"}mesh")
            if mesh is not None:
                vs=[[float(v.get(k)) for k in "xyz"] for v in mesh.findall("{"+NS+"}vertices/{"+NS+"}vertex")]
                for a in transforms:
                    vs=[apply(v,a) for v in vs]
                ts=[[int(t.get(k)) for k in ("v1","v2","v3")] for t in mesh.findall("{"+NS+"}triangles/{"+NS+"}triangle")]
                return [dict(vertices=vs,triangles=ts)]
            results=[]
            for c in obj.findall("{"+NS+"}components/{"+NS+"}component"):
                dest=c.get("{"+PNS+"}path",name).lstrip("/")
                if ".." in Path(dest).parts or "\\" in dest: raise ValueError("MODEL: unsafe path")
                results+=load(dest,c.get("objectid"),[matrix(c.get("transform"))]+transforms,stack|{key})
            if not results: raise ValueError("MODEL: empty object")
            return results
        root=xml("3D/3dmodel.model")
        cfg=xml("Metadata/model_settings.config")
        names={o.get("id"):next((m.get("value") for m in o.findall("metadata") if m.get("key")=="name"),None) for o in cfg.findall("object")}
        parts=[]
        for item in root.findall("{"+NS+"}build/{"+NS+"}item"):
            if item.get("printable","1")!="1": raise ValueError("MODEL: nonprintable item")
            result=load("3D/3dmodel.model",item.get("objectid"),[matrix(item.get("transform"))],set())
            if len(result)!=1: raise ValueError("PART: expected one mesh per named part")
            result[0]["name"]=names.get(item.get("objectid"));parts+=result
        return dict(parts=parts,settings=json.loads(z.read("Metadata/project_settings.config")),
                    gcodes=[n for n in z.namelist() if n.endswith(".gcode")],
                    warnings=z.read("Metadata/slice_info.config").decode() if "Metadata/slice_info.config" in z.namelist() else None)

def compare_projects(expected,actual):
    """Detect changed composition, scale, placement and material after real slicing."""
    if sorted(p["name"] for p in expected["parts"])!=sorted(p["name"] for p in actual["parts"]):
        raise ValueError("DELIVERY: part set changed")
    for key in ('printer_settings_id', 'curr_bed_type', 'filament_type', 'nozzle_diameter', 'layer_height', 'wall_loops', 'sparse_infill_density', 'brim_type', 'brim_width', 'enable_support', 'support_type', 'support_on_build_plate_only', 'support_top_z_distance', 'nozzle_temperature', 'nozzle_temperature_initial_layer', 'textured_plate_temp', 'textured_plate_temp_initial_layer', 'filament_max_volumetric_speed', 'filament_flow_ratio', 'xy_hole_compensation', 'xy_contour_compensation', 'filament_shrink', 'filament_shrinkage_compensation_z'):
        if expected["settings"].get(key)!=actual["settings"].get(key):
            raise ValueError("DELIVERY: setting changed: "+key)
    by_name={p["name"]:p for p in actual["parts"]}
    for p in expected["parts"]:
        q=by_name[p["name"]]
        def points(m):
            return m["vertices"]
        a,b=points(p),points(q)
        if len(a)!=len(b) or any(abs(x-y)>0.00005 for aa,bb in zip(a,b) for x,y in zip(aa,bb)):
            raise ValueError("DELIVERY: mesh/placement changed: "+p["name"])
        if p["triangles"]!=q["triangles"]: raise ValueError("DELIVERY: triangle topology changed")
        mesh_report(q)
    return True
