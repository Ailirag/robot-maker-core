"""Calculated deposited model mass/centroid from single-material Bambu G-code.

Separates part roles from supports, brim and purge. Reports toolpath estimates,
never measured facts. Unknown modes/roles/identities refuse attribution.
"""
from __future__ import annotations
import hashlib,json,math,re,zipfile
import xml.etree.ElementTree as ET

MODEL_ROLES=frozenset(("Outer wall","Inner wall","Sparse infill","Internal solid infill",
    "Top surface","Bottom surface","Bridge","Internal Bridge","Gap infill","Overhang wall","Ironing","Floating vertical shell"))
WASTE_ROLES=frozenset(("Custom","Brim","Skirt","Support","Support interface","Support transition",
    "Prime tower","Flush","Wipe tower"))
SCHEMA="slicer-model-masses-v1"

def deposition(text,*,diameter_mm,density_g_cm3,default_object=None):
    if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and v>0
               for v in (diameter_mm,density_g_cm3)):
        raise ValueError("Positive filament diameter and density required")
    factor=math.pi*(diameter_mm/2)**2*density_g_cm3/1000
    xyz=[None,None,None];absolute=True;eabs=True;e=0.;debt=0.;role="Custom";obj=default_object
    objects={};excluded=0.;excluded_roles={};plane="G17"
    for line in text.splitlines():
        start=re.fullmatch(r"; start printing object, unique label id: (\d+)",line.strip())
        if start: obj=start[1];continue
        if line.strip().startswith("; stop printing object"):
            obj=None;continue
        if line.strip().startswith("; FEATURE:"):
            role=line.split(":",1)[1].strip();continue
        words=line.split(";",1)[0].strip().split()
        if not words: continue
        cmd=words[0].upper()
        values={k:float(v) for k,v in re.findall(r"([XYZEFIJKRDS])([-+]?(?:\d+(?:\.\d*)?|\.\d+))"," ".join(words[1:]).upper())}
        if not all(math.isfinite(v) for v in values.values()): raise ValueError("Nonfinite G-code")
        if cmd=="G20" or cmd=="M200" or (cmd.startswith("T") and cmd not in {"T0","T255","T1000","T65535"}):
            raise ValueError("Unsupported units, volumetric extrusion or tool")
        if cmd in {"G17","G18","G19"}: plane=cmd
        if cmd=="G90": absolute=True;eabs=True
        if cmd=="G91": absolute=False;eabs=False
        if cmd=="M82": eabs=True
        if cmd=="M83": eabs=False
        if cmd=="G92":
            for i,axis in enumerate("XYZ"):
                if axis in values: xyz[i]=values[axis]
            if "E" in values: e=values["E"]
        if cmd not in {"G0","G1","G2","G3"}: continue
        old=list(xyz)
        for i,axis in enumerate("XYZ"):
            if axis in values:
                xyz[i]=values[axis] if absolute else (None if xyz[i] is None else xyz[i]+values[axis])
        delta=0.
        if "E" in values:
            target=values["E"] if eabs else e+values["E"];delta=target-e;e=target
        if delta<0: debt-=delta;continue
        recovery=min(debt,delta);debt-=recovery;delta-=recovery
        if delta<=0: continue
        mass=delta*factor
        if cmd in {"G2","G3"} and (plane!="G17" or "R" in values or not {"I","J"}<=set(values)):
            raise ValueError("Unsupported arc definition")
        moving=any(a!=b for a,b in zip(old,xyz)) or cmd in {"G2","G3"}
        if not moving or role in WASTE_ROLES:
            excluded+=mass;excluded_roles[role]=excluded_roles.get(role,0)+mass;continue
        if role not in MODEL_ROLES: raise ValueError("Unknown extrusion feature: "+role)
        if obj is None: raise ValueError("Model extrusion lacks object identity")
        if any(x is None for x in old+xyz): raise ValueError("Unknown deposition coordinates")
        center=[(a+b)/2 for a,b in zip(old,xyz)]
        if cmd in {"G2","G3"}:
            if plane!="G17" or "R" in values or not {"I","J"}<=set(values):
                raise ValueError("Unsupported arc definition")
            cx=old[0]+values["I"];cy=old[1]+values["J"]
            radius=math.hypot(values["I"],values["J"])
            if radius<=0 or abs(math.hypot(xyz[0]-cx,xyz[1]-cy)-radius)>max(.02,radius*.001):
                raise ValueError("Invalid arc radius")
            a=math.atan2(old[1]-cy,old[0]-cx);b=math.atan2(xyz[1]-cy,xyz[0]-cx)
            sweep=(b-a)%(2*math.pi) if cmd=="G3" else -((a-b)%(2*math.pi))
            if abs(sweep)<1e-12: sweep=2*math.pi if cmd=="G3" else -2*math.pi
            center[:2]=[cx+radius*(math.sin(a+sweep)-math.sin(a))/sweep,
                         cy+radius*(math.cos(a)-math.cos(a+sweep))/sweep]
        entry=objects.setdefault(obj,dict(mass_g=0.,moment_g_mm=[0.,0.,0.]))
        entry["mass_g"]+=mass
        entry["moment_g_mm"]=[a+mass*b for a,b in zip(entry["moment_g_mm"],center)]
    return dict(objects={k:dict(mass_g=v["mass_g"],com_bed_mm=[x/v["mass_g"] for x in v["moment_g_mm"]])
                         for k,v in objects.items()},
                excluded_mass_g=excluded,excluded_roles_g=excluded_roles)

def project_masses(path):
    """Read object IDs from this exact sliced archive, not object list position."""
    with zipfile.ZipFile(path) as z:
        if len(z.namelist())!=len(set(z.namelist())): raise ValueError("Duplicate archive members")
        cfg=ET.fromstring(z.read("Metadata/model_settings.config"))
        settings=json.loads(z.read("Metadata/project_settings.config"))
        def scalar(key):
            value=settings[key]
            if isinstance(value,list):
                if len(value)!=1: raise ValueError("Single filament required")
                value=value[0]
            return float(value)
        names={}
        for o in cfg.findall("object"):
            name=next((x.get("value") for x in o.findall("metadata") if x.get("key")=="name"),None)
            if not name or name in names.values(): raise ValueError("Missing/duplicate part name")
            names[o.get("id")]=name
        results={};waste=0.;sources=[]
        for plate in cfg.findall("plate"):
            meta={m.get("key"):m.get("value") for m in plate.findall("metadata")}
            gcode=meta.get("gcode_file")
            if not gcode: raise ValueError("Unsliced plate")
            identities={}
            for instance in plate.findall("model_instance"):
                m={x.get("key"):x.get("value") for x in instance.findall("metadata")}
                label=m["identify_id"];name=names[m["object_id"]]
                if label in identities or name in results: raise ValueError("Ambiguous object instances")
                identities[label]=name
            raw=z.read(gcode);r=deposition(raw.decode("utf-8"),
                diameter_mm=scalar("filament_diameter"),density_g_cm3=scalar("filament_density"),
                default_object=next(iter(identities)) if len(identities)==1 and b"; start printing object" not in raw else None)
            if set(r["objects"])!=set(identities): raise ValueError("G-code/model inventory mismatch")
            for key,value in r["objects"].items(): results[identities[key]]=value
            waste+=r["excluded_mass_g"];sources.append(dict(path=gcode,sha256=hashlib.sha256(raw).hexdigest()))
    return dict(schema=SCHEMA,provenance="CALCULATED",physical_approval=False,
        source_sha256=hashlib.sha256(__import__("pathlib").Path(path).read_bytes()).hexdigest(),
        method="deposited filament by object and model feature; density from sliced project",
        objects=results,excluded_mass_g=waste,gcodes=sources)
