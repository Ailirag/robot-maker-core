"""Per-object strength settings layered over the sealed base project format.

The baseline serializer is retained byte-for-byte for existing hash-bound
deliveries. This extension adds settings and stronger round-trip checks.
"""
from __future__ import annotations
import io
import math
import xml.etree.ElementTree as ET
import zipfile
from engineering import print_package as base

PART_SETTING_KEYS = frozenset(("wall_loops", "sparse_infill_density", "top_shell_layers", "bottom_shell_layers"))

def validate_part_settings(settings):
    if not isinstance(settings,dict) or not set(settings)<=PART_SETTING_KEYS:
        raise ValueError("SETTING: unsupported per-part setting")
    for key,value in settings.items():
        if not isinstance(value,str):
            raise ValueError("SETTING: values must be canonical strings")
        if key=="sparse_infill_density":
            if not value.endswith("%"):
                raise ValueError("SETTING: infill requires percent")
            try: number=float(value[:-1])
            except ValueError: raise ValueError("SETTING: invalid infill")
            if not math.isfinite(number) or not 0<=number<=100:
                raise ValueError("SETTING: infill outside 0..100")
        elif not value.isascii() or not value.isdigit() or str(int(value))!=value or int(value)<(1 if key=="wall_loops" else 0):
            raise ValueError("SETTING: invalid shell count")
    return settings

def project_bytes(parts,settings,title):
    for p in parts: validate_part_settings(p.get("print_settings",{}))
    original=base.project_bytes(parts,settings,title)
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        cfg=ET.fromstring(source.read("Metadata/model_settings.config"))
        for obj,p in zip(cfg.findall("object"),parts,strict=True):
            for k,v in sorted(p.get("print_settings",{}).items()):
                ET.SubElement(obj,"metadata",key=k,value=v)
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as out:
            for info in source.infolist():
                data=(ET.tostring(cfg,encoding="utf-8",xml_declaration=True)
                      if info.filename=="Metadata/model_settings.config" else source.read(info.filename))
                out.writestr(info,data)
    return stream.getvalue()

def inspect_project(path):
    loaded=base.inspect_project(path)
    with zipfile.ZipFile(path) as source:
        cfg=ET.fromstring(source.read("Metadata/model_settings.config"))
    overrides={};ids=set()
    for obj in cfg.findall("object"):
        if obj.get("id") in ids: raise ValueError("SETTING: duplicate object ID")
        ids.add(obj.get("id"))
        name=next((m.get("value") for m in obj.findall("metadata") if m.get("key")=="name"),None)
        if not name or name in overrides: raise ValueError("SETTING: missing/duplicate part name")
        volumes=obj.findall("part")
        if len(volumes)!=1 or volumes[0].get("subtype")!="normal_part":
            raise ValueError("SETTING: modifiers and multiple volumes are unsupported")
        values={}
        for container in [obj,*volumes]:
            seen=set()
            for m in container.findall("metadata"):
                key=m.get("key")
                if key not in PART_SETTING_KEYS:
                    if key not in {"name","extruder"} and key in loaded["settings"]:
                        raise ValueError("SETTING: unsupported per-part process override: "+key)
                    if key=="extruder" and m.get("value")!="1":
                        raise ValueError("SETTING: unsupported per-part extruder")
                    continue
                if key in seen: raise ValueError("SETTING: duplicate per-part setting")
                seen.add(key)
                if key in values and values[key]!=m.get("value"):
                    raise ValueError("SETTING: conflicting object/volume overrides")
                values[key]=m.get("value")
        overrides[name]=validate_part_settings(values)
    names=[p["name"] for p in loaded["parts"]]
    if len(names)!=len(set(names)) or set(names)!=set(overrides):
        raise ValueError("SETTING: configuration part inventory differs")
    for p in loaded["parts"]: p["print_settings"]=overrides[p["name"]]
    return loaded

def compare_projects(expected,actual):
    base.compare_projects(expected,actual)
    for key in ("top_shell_layers","bottom_shell_layers","sparse_infill_pattern"):
        if expected["settings"].get(key)!=actual["settings"].get(key):
            raise ValueError("DELIVERY: setting changed: "+key)
    by_name={p["name"]:p for p in actual["parts"]}
    for p in expected["parts"]:
        if p.get("print_settings",{})!=by_name[p["name"]].get("print_settings",{}):
            raise ValueError("DELIVERY: part setting changed: "+p["name"])
    return True
