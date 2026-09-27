"""Saved print inputs and calibration decisions; never grants a physical release.

Supported 3MF subset: one printable instance, one mesh/part and one filament,
positive axis-aligned transforms, rectangular bed without excluded areas.
Unsupported settings/geometry composition fails explicitly instead of guessing.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

from engineering.facts import FactManifest

NS = {"c": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
PRODUCTION = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
IDENTITY = "1 0 0 0 1 0 0 0 1 0 0 0"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def number(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError("NUMBER: boolean is not a dimension")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("NUMBER: finite numeric value required") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("NUMBER: finite positive value required")
    return result


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"INPUT: {label} is required")
    return value


def _xml(data):
    text = data.decode("utf-8-sig")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML: declarations/entities are unsupported")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("XML: malformed document") from exc


def _transform(text):
    values = [number(v) for v in (text or IDENTITY).split()]
    if len(values) != 12 or any(values[i] != 0 for i in (1, 2, 3, 5, 6, 7)):
        raise ValueError("TRANSFORM: only positive axis-aligned transforms supported")
    if any(values[i] <= 0 for i in (0, 4, 8)):
        raise ValueError("TRANSFORM: reflected/zero axes unsupported")
    return values


def _array(settings, key):
    value = settings.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"INPUT: {key} array required")
    return value


def _single(settings, key):
    value = _array(settings, key)
    if len(value) != 1:
        raise ValueError(f"FILAMENT: one {key} required")
    return value[0]


def inspect_project(path, *, expected_sha256=None):
    path = Path(path)
    raw = path.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and source_hash != expected_sha256:
        raise ValueError("SOURCE_CHANGED: 3MF differs from bound input")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ValueError("ZIP: invalid 3MF") from exc
    with archive as z:
        names = z.namelist()
        if len(names) != len(set(names)) or len(names) > 10000:
            raise ValueError("ZIP: duplicate/excessive entries")
        if any(PurePosixPath(n).is_absolute() or ".." in PurePosixPath(n).parts or "\\" in n for n in names):
            raise ValueError("ZIP: unsafe member path")
        if sum(i.file_size for i in z.infolist()) > 256 * 1024 * 1024:
            raise ValueError("ZIP: expanded size exceeds supported project")
        def read(name):
            try:
                return z.read(name)
            except KeyError as exc:
                raise ValueError(f"INPUT: missing {name}") from exc
        settings = json.loads(read("Metadata/project_settings.config"))
        if not isinstance(settings, dict):
            raise ValueError("INPUT: project settings object required")

        # These are saved setpoints, not measured temperatures or inferred defaults.
        _text(settings.get("print_settings_id"), "process preset")
        _text(settings.get("printer_settings_id"), "printer preset")
        number(settings.get("layer_height"), positive=True)
        for key in ("nozzle_diameter", "nozzle_temperature_initial_layer", "nozzle_temperature"):
            for value in _array(settings, key):
                number(value, positive=True)
        plate_keys = {"Textured PEI Plate":"textured_plate_temp",
                      "Smooth PEI Plate / High Temp Plate":"hot_plate_temp",
                      "High Temp Plate":"hot_plate_temp",
                      "Cool Plate":"cool_plate_temp", "Engineering Plate":"eng_plate_temp"}
        plate_key = plate_keys.get(settings.get("curr_bed_type"))
        if plate_key is None:
            raise ValueError("INPUT: unsupported/missing plate profile")
        for key in (plate_key, plate_key + "_initial_layer"):
            for value in _array(settings, key):
                number(value, positive=True)
        applied = json.loads(read("Metadata/calibration.json")) if "Metadata/calibration.json" in names else None
        root = _xml(read("3D/3dmodel.model"))
        if root.attrib.get("unit") != "millimeter":
            raise ValueError("UNIT: millimeter model required")
        objects = root.findall("c:resources/c:object", NS)
        items = root.findall("c:build/c:item", NS)
        if len(items) != 1 or len(objects) != 1 or items[0].get("printable", "1") != "1":
            raise ValueError("MODEL: one printable object/instance required")
        if items[0].get("objectid") != objects[0].get("id"):
            raise ValueError("MODEL: dangling build reference")
        transforms = [_transform(items[0].get("transform"))]
        obj = objects[0]
        components = obj.findall("c:components/c:component", NS)
        if components:
            if len(components) != 1:
                raise ValueError("MODEL: one component required")
            comp = components[0]
            transforms.append(_transform(comp.get("transform")))
            ref = comp.get(PRODUCTION + "path", "").lstrip("/")
            if not ref or ".." in PurePosixPath(ref).parts:
                raise ValueError("MODEL: unsupported component reference")
            external = _xml(read(ref))
            if external.get("unit") != "millimeter":
                raise ValueError("UNIT: millimeter component required")
            matches = external.findall("c:resources/c:object", NS)
            if len(matches) != 1 or matches[0].get("id") != comp.get("objectid"):
                raise ValueError("MODEL: unsupported/dangling component")
            obj = matches[0]
        mesh = obj.find("c:mesh", NS)
        if mesh is None:
            raise ValueError("MODEL: mesh required")
        vertices = [[number(v.get(a)) for a in "xyz"] for v in mesh.findall("c:vertices/c:vertex", NS)]
        triangles = []
        for tri in mesh.findall("c:triangles/c:triangle", NS):
            try:
                indices = [int(tri.attrib[k]) for k in ("v1", "v2", "v3")]
            except (KeyError, ValueError) as exc:
                raise ValueError("MESH: invalid triangle") from exc
            if len(set(indices)) != 3 or any(i < 0 or i >= len(vertices) for i in indices):
                raise ValueError("MESH: invalid vertex reference")
            triangles.append(indices)
        if not vertices or not triangles:
            raise ValueError("MESH: empty geometry")
        config = _xml(read("Metadata/model_settings.config"))
        configs = config.findall("./object")
        if len(configs) != 1 or configs[0].get("id") != objects[0].get("id"):
            raise ValueError("OVERRIDE: one matching object settings required")
        for item in configs[0].findall("metadata"):
            if item.get("key") not in ("name", "extruder", None) or (item.get("key") == "extruder" and item.get("value") != "1"):
                raise ValueError("OVERRIDE: object process/extruder override unsupported")
        parts = configs[0].findall("part")
        if len(parts) != 1 or parts[0].get("subtype", "normal_part") != "normal_part":
            raise ValueError("OVERRIDE: one normal part required")
        allowed = {"name", "matrix", "source_file", "source_object_id", "source_volume_id",
                   "source_offset_x", "source_offset_y", "source_offset_z"}
        offsets = {}
        for item in parts[0].findall("metadata"):
            key, value = item.get("key"), item.get("value")
            if key not in allowed:
                raise ValueError("OVERRIDE: part setting unsupported")
            if key == "matrix" and [number(x) for x in value.split()] != [1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]:
                raise ValueError("TRANSFORM: source part matrix unsupported")
            if key.startswith("source_offset_"):
                offsets[key[-1]] = number(value)
        # Plate overrides and extra parts cannot silently alter inherited settings.
        for plate in config.findall("./plate"):
            allowed_plate = {"plater_id", "plater_name", "locked", "filament_map_mode", "filament_maps",
                             "filament_volume_maps", "thumbnail_file", "thumbnail_no_light_file",
                             "top_file", "pick_file"}
            if any(m.get("key") not in allowed_plate for m in plate.findall("metadata")):
                raise ValueError("OVERRIDE: plate setting unsupported")
        material = _text(_single(settings, "filament_type"), "filament type")
        preset = _text(_single(settings, "filament_settings_id"), "filament preset")
        shrink_text = _single(settings, "filament_shrink")
        if not isinstance(shrink_text, str) or not shrink_text.endswith("%"):
            raise ValueError("NUMBER: filament shrink requires percent")
        shrink = number(shrink_text[:-1], positive=True)
        compensations = {k: number(settings.get(k)) for k in
                         ("xy_hole_compensation", "xy_contour_compensation",
                          "elefant_foot_compensation", "circle_compensation_manual_offset")}
        if settings.get("enable_circle_compensation") not in ("0", "1"):
            raise ValueError("INPUT: explicit circle compensation flag required")
        area = settings.get("printable_area")
        try:
            points = [[number(v) for v in point.split("x")] for point in area]
        except (TypeError, AttributeError) as exc:
            raise ValueError("BED: rectangular coordinates required") from exc
        if len(points) != 4 or any(len(p) != 2 for p in points) or settings.get("bed_exclude_area") != []:
            raise ValueError("BED: rectangle without excluded areas required")
        width, depth = max(p[0] for p in points), max(p[1] for p in points)
        if sorted(points) != sorted([[0,0],[width,0],[width,depth],[0,depth]]) or min(width, depth) <= 0:
            raise ValueError("BED: origin-aligned rectangle required")
        height = number(settings.get("printable_height"), positive=True)
        scales = [math.prod(t[i] for t in transforms) for i in (0,4,8)]
        members = {n: hashlib.sha256(read(n)).hexdigest() for n in sorted(names)}
    return {"schema":"saved-print-project-v1", "path":str(path.resolve()), "sha256":source_hash,
            "members":members, "compensation_applied":applied, "settings":settings, "printer":_text(settings.get("printer_model"), "printer"),
            "material_type":material, "material_preset":preset, "bed_mm":[width,depth,height],
            "model_scale_xyz":scales, "filament_shrink_pct":shrink, "compensations":compensations,
            "circle_compensation":settings["enable_circle_compensation"] == "1",
            "mesh":{"vertices":len(vertices),"triangles":len(triangles),
                    "bounds_mm":[[min(v[i] for v in vertices) for i in range(3)],
                                 [max(v[i] for v in vertices) for i in range(3)]],
                    "digest":digest({"vertices":vertices,"triangles":triangles}), "source_offsets":offsets},
            "physical_approval":False, "telemetry":False}


def _fact(manifest, fact_id, scope, unit):
    result = manifest.resolve(fact_id, expected_unit=unit,
                              allowed_provenance={"MEASURED", "BENCH_VALIDATED"},
                              require_datum=True, expected_scope=scope)
    if result.status != "READY" or not result.source or not result.datum:
        raise ValueError(f"FACT: {fact_id}: {result.status}: explicit source/datum required")
    return result.as_dict()


def analyse(recipe, facts, project):
    if recipe.get("schema") != "print-calibration-recipe-v1":
        raise ValueError("SCHEMA: unsupported calibration recipe")
    _text(recipe.get("id"), "calibration ID")
    scope = _text(recipe.get("scope"), "scope")
    if recipe.get("source_project_sha256") != project["sha256"]:
        raise ValueError("SOURCE_CHANGED: recipe belongs to a different project")
    if "Metadata/calibration.json" in project["members"]:
        raise ValueError("DOUBLE_COMPENSATION: source geometry already has a compensation marker")
    material = recipe.get("material", {})
    if (material.get("type") != project["material_type"] or
        material.get("preset") != project["material_preset"] or
        recipe.get("printer") != project["printer"]):
        raise ValueError("MATERIAL: printer/material/preset mismatch")
    _text(material.get("manufacturer"), "physical material manufacturer")
    if (project["model_scale_xyz"] != [1,1,1] or project["filament_shrink_pct"] != 100 or
        project["circle_compensation"] or
        any(project["compensations"][k] != 0 for k in
            ("xy_hole_compensation", "xy_contour_compensation", "circle_compensation_manual_offset"))):
        raise ValueError("DOUBLE_COMPENSATION: geometry route requires neutral model/XY/shrink settings")
    if recipe.get("z_policy") != "unchanged" or number(recipe.get("z_factor"), positive=True) != 1:
        raise ValueError("Z_POLICY: this contract requires an explicit unchanged Z")
    manifest = facts if isinstance(facts, FactManifest) else FactManifest(facts)
    candidates, consumed, observations, intervals = {}, {}, [], []
    rows = recipe.get("measurements")
    if not isinstance(rows, list) or not rows:
        raise ValueError("INPUT: calibration measurements required")
    for row in rows:
        key = _text(row.get("id"), "measurement ID")
        if key in candidates:
            raise ValueError("DUPLICATE: measurement ID")
        nominal = number(row.get("nominal_mm"), positive=True)
        tolerance = number(row.get("tolerance_mm"), positive=True)
        _text(row.get("nominal_source"), "nominal source")
        fact = _fact(manifest, row.get("fact_id"), scope, "mm")
        measured = number(fact["value"], positive=True)
        axis = row.get("axis")
        if axis not in ("xy", "z"):
            raise ValueError("INPUT: measurement axis must be xy or z")
        factor = nominal / measured
        candidates[key] = {"factor":factor, "axis":axis, "nominal_mm":nominal, "measured_mm":measured,
                           "tolerance_mm":tolerance}
        consumed[row["fact_id"]] = fact
        observations.append(dict(row))
        if axis == "xy":
            intervals.append(((nominal-tolerance)/measured, (nominal+tolerance)/measured))

    for context in recipe.get("context_facts", []):
        fid = context.get("fact_id")
        fact = _fact(manifest, fid, scope, context.get("unit"))
        if fact["value"] != context.get("expected_value"):
            raise ValueError("CONTEXT: measured printer/material context changed: "+str(fid))
        consumed[fid] = fact
    selected = recipe.get("selection")
    if selected not in candidates or candidates[selected]["axis"] != "xy":
        raise ValueError("SELECTION: explicit XY measurement required")
    xy = candidates[selected]["factor"]
    if not 0.9 <= xy <= 1.1:
        raise ValueError("NUMBER: proposed scale outside supported calibration range")

    targets = recipe.get("control_targets", observations)
    if not isinstance(targets, list) or not targets:
        raise ValueError("CONTROL: explicit measurement targets required")
    target_map = {}
    for row in targets:
        key = _text(row.get("id"), "control target ID")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key) or key in target_map:
            raise ValueError("CONTROL: safe unique target IDs required")
        number(row.get("nominal_mm"), positive=True)
        number(row.get("tolerance_mm"), positive=True)
        _text(row.get("nominal_source"), "control target source")
        target_map[key] = row
    if not set(candidates) <= set(target_map):
        raise ValueError("CONTROL: baseline features must remain in control plan")
    payload = {"recipe":{k:v for k,v in recipe.items() if k != "control"},
               "project_sha256":project["sha256"], "project_settings_digest":digest(project["settings"]),
               "consumed":consumed, "factors":[xy,xy,1.0]}
    baseline = digest(payload)
    status, control_results = "TRIAL_ONLY", []
    control = recipe.get("control")
    if control is not None:
        if not isinstance(control, dict) or not {"baseline_digest","trial_artifact_sha256","scope","artifact_fact_id","measurements"} <= control.keys():
            raise ValueError("CONTROL: complete bound record required")
        if control["baseline_digest"] != baseline:
            raise ValueError("CONTROL_STALE: baseline inputs changed")
        sha = control["trial_artifact_sha256"]
        if not isinstance(sha, str) or not re.fullmatch("[0-9a-f]{64}", sha):
            raise ValueError("CONTROL: artifact SHA256 required")
        control_scope = _text(control["scope"], "control scope")
        artifact_fact = _fact(manifest, control["artifact_fact_id"], control_scope, "text")
        if artifact_fact["value"] != sha:
            raise ValueError("CONTROL: measured job binding differs")
        controls = control["measurements"]
        if not isinstance(controls, dict) or set(controls) != set(target_map):
            raise ValueError("CONTROL: every baseline feature must be checked")
        consumed[control["artifact_fact_id"]] = artifact_fact
        for key, fact_id in controls.items():
            fact = _fact(manifest, fact_id, control_scope, "mm")
            value = number(fact["value"], positive=True)
            target = target_map[key]
            error = abs(value-target["nominal_mm"])
            control_results.append({"id":key,"value_mm":value,"error_mm":error,
                                    "pass":error <= target["tolerance_mm"]})
            consumed[fact_id] = fact
        status = "CONTROL_VERIFIED" if all(row["pass"] for row in control_results) else "CONTROL_FAILED"
    result = {"schema":"print-calibration-plan-v1", "id":recipe["id"], "status":status,
              "baseline_digest":baseline, "source_project_sha256":project["sha256"],
              "project_settings_digest":digest(project["settings"]),
              "material":copy.deepcopy(material), "factors":[xy,xy,1.0], "selection":selected,
              "candidates":candidates, "observations":observations, "control_targets":copy.deepcopy(targets),
              "measurement_intervals_overlap":bool(intervals) and max(v[0] for v in intervals) <= min(v[1] for v in intervals),
              "consumed_fact_ids":sorted(consumed), "consumed_facts":consumed,
              "control_results":control_results, "physical_approval":False}
    result["digest"] = digest(result)
    return result


def control_proposal(report, receipt, *, source, scope):
    """Convert an explicitly confirmed owner receipt to proposed facts, without writing authorities."""
    if (receipt.get("schema") != "print-control-observations-v1" or
        receipt.get("kind") != "CONTROL_OBSERVATIONS" or
        receipt.get("job_confirmed") is not True or receipt.get("cooled") is not True or
        receipt.get("physical_approval") is not False):
        raise ValueError("CONTROL: confirmed job, cooling and observations required")
    plan = report["plan"]
    sha = report["artifacts"]["PRINT_JOB.3mf"]["sha256"]
    if receipt.get("baseline_digest") != plan["baseline_digest"] or receipt.get("trial_artifact_sha256") != sha:
        raise ValueError("CONTROL: receipt belongs to another baseline/artifact")
    for key in ("instrument", "measured_at"):
        if not isinstance(receipt.get(key), str) or not receipt[key].strip():
            raise ValueError("CONTROL: instrument and measurement date required")
    _text(source, "receipt source")
    _text(scope, "control scope")
    targets = {row["id"]:row for row in plan["control_targets"]}
    values = receipt.get("observations")
    if not isinstance(values, dict) or set(values) != set(targets):
        raise ValueError("CONTROL: complete set of target keys required")
    meta = dict(source=source,scope=scope,provenance="MEASURED")
    parameters = {"control_printed_job":dict(meta,value=sha,unit="text",
                                           datum="Owner-confirmed cooled print from the bound trial artifact")}
    mapping = {}
    for key,row in targets.items():
        raw = values[key]
        if not isinstance(raw, dict) or "value" not in raw:
            raise ValueError("CONTROL: observation record required")
        value = raw["value"]
        if value is not None: value = number(value,positive=True)
        fid = "control_" + key
        parameters[fid] = dict(meta,value=value,unit="mm",
                               datum=row.get("label",key)+"; "+receipt["instrument"]+"; "+receipt["measured_at"])
        if value is None:
            parameters[fid].update(provenance="UNKNOWN",obtained_by="Measure the cooled trial: "+row.get("label",key))
        mapping[key] = fid
    return {"schema":"print-control-fact-proposal-v1", "parameters":parameters,
            "control":{"baseline_digest":plan["baseline_digest"],"trial_artifact_sha256":sha,
                       "scope":scope,"artifact_fact_id":"control_printed_job","measurements":mapping},
            "receipt":copy.deepcopy(receipt),"physical_approval":False}
