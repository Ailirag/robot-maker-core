"""Build and verify a native multi-plate project; reuse only exact bound inputs.

Cache directories are immutable. Read-only freshness never starts a slicer.
Successful native output is sealed only after geometry, settings, plate/material
assignments and actual first-layer moves have been checked.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

from engineering import multiplate as M
from engineering.delivery import read_bound

MANIFEST = "COMBINED.json"


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def inputs(root, plan):
    from engineering.delivery_assembly import verify_inventory
    contracts = [json.loads(read_bound(root, pin)) for pin in plan["deliveries"]]
    verify_inventory(root, plan, contracts)
    return M.sources_from_contracts(root, contracts)


def location(root, plan, prepared):
    config = plan.get("combined_project", {})
    relative = config.get("cache_directory")
    name = config.get("filename")
    root = Path(root).resolve()
    if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
            or ".." in Path(relative).parts):
        raise ValueError("MULTIPLATE: combined cache directory is required")
    directory = (root / relative / M.sha(prepared)).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("MULTIPLATE: cache outside root")
    if (not isinstance(name, str) or Path(name).name != name or "\\" in name
            or ":" in name or not name.lower().endswith(".3mf") or name == "prepared.3mf"):
        raise ValueError("MULTIPLATE: combined output filename is required")
    return directory, name


def first_layer_speeds(raw, height, limit):
    feature = ""
    z = None
    feed = 0.
    observed = {}
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("; FEATURE:"):
            feature = line.partition(":")[2].strip()
        if line.startswith("; Z_HEIGHT:"):
            z = float(line.partition(":")[2])
        if not line.startswith(("G0 ", "G1 ")):
            continue
        values = {k: float(v) for k, v in
                  re.findall(r"([XYZEF])(-?(?:\d+\.?\d*|\.\d+))", line)}
        feed = values.get("F", feed)
        if (z is not None and abs(z - height) < 1e-5 and values.get("E", 0) > 0
                and ("X" in values or "Y" in values)
                and feature in ("Brim", "Outer wall", "Inner wall")):
            observed[feature] = max(observed.get(feature, 0), feed / 60)
    if not observed or any(not math.isfinite(v) or v <= 0 or v > limit + .01
                           for v in observed.values()):
        raise ValueError("MULTIPLATE: first layer speed not honored by native slicer")
    return observed


@lru_cache(maxsize=2)
def _validate_output(prepared, sliced, receipt):
    proof = M.verify_prepared(io.BytesIO(sliced), prepared)
    view = M.inspect(io.BytesIO(sliced))
    result = M.validate_native_receipt(json.loads(receipt), proof["plates"])
    estimates = []
    with zipfile.ZipFile(io.BytesIO(sliced)) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError("MULTIPLATE: duplicate output ZIP member")
        expected = {f"Metadata/plate_{i+1}.gcode" for i in range(proof["plates"])}
        if set(view["gcodes"]) != expected:
            raise ValueError("MULTIPLATE: every plate requires its own G-code")
        info = M.xml(z.read("Metadata/slice_info.config"))
        nodes = info.findall("plate")
        if [M.metadata(p).get("index") for p in nodes] != [str(i+1) for i in range(proof["plates"])]:
            raise ValueError("MULTIPLATE: native slice metadata plate indices differ")
        for plate, node, row in zip(view["plates"], nodes, result["sliced_plates"]):
            slots = {int(obj["extruder"]) for obj in plate["objects"].values()}
            if len(slots) != 1:
                raise ValueError("MULTIPLATE: single material per plate required")
            slot = slots.pop()
            material = view["settings"]["filament_type"][slot-1]
            filament = node.findall("filament")
            if (len(filament) != 1 or filament[0].get("id") != str(slot)
                    or filament[0].get("type") != material
                    or [f.get("id") for f in row.get("filaments", [])] != [slot]):
                raise ValueError("MULTIPLATE: native material assignment differs")
            names = sorted(plate["names"])
            if (sorted(o.get("name") for o in row.get("objects", [])) != names
                    or sorted(o.get("name") for o in node.findall("object")
                              if o.get("skipped") == "false") != names):
                raise ValueError("MULTIPLATE: native object inventory differs")
            if M.metadata(node).get("outside") != "false":
                raise ValueError("MULTIPLATE: native plate outside print area")
            seconds = row["total_predication"]
            grams = sum(f["total_used_g"] for f in row["filaments"])
            if any(not math.isfinite(v) or v <= 0 for v in (seconds, grams)):
                raise ValueError("MULTIPLATE: native estimates are missing")
            raw = z.read(f'Metadata/plate_{plate["index"]}.gcode')
            if b"; CHANGE_LAYER" not in raw:
                raise ValueError("MULTIPLATE: empty G-code")
            speeds = {}
            if "initial_layer_speed" in view["settings"]:
                limit = min(map(float, view["settings"]["initial_layer_speed"]))
                height = float(view["settings"]["initial_layer_print_height"])
                speeds = first_layer_speeds(raw, height, limit)
            estimates.append(dict(index=plate["index"], title=plate["title"],
                material=material, names=plate["names"], seconds=seconds, grams=grams,
                gcode_sha256=M.sha(raw), first_layer_speeds_mm_s=speeds))
    version = info.find("header/header_item[@key='X-BBL-Client-Version']")
    proof.update(jobs=estimates, slicer_version=version.get("value") if version is not None else None,
                 physical_approval=False)
    return proof


def validate_output(prepared, sliced, receipt):
    # The key is file content, not a path or mtime; mutations cannot hit this cache.
    return copy.deepcopy(_validate_output(prepared, sliced, receipt))


def _pin(root, path):
    raw = path.read_bytes()
    return dict(path=path.relative_to(Path(root).resolve()).as_posix(),
                sha256=M.sha(raw), bytes=len(raw))


def current(root, plan, *, prepared=None):
    if prepared is None:
        prepared = M.prepare(inputs(root, plan))
    directory, name = location(root, plan, prepared)
    manifest = directory / MANIFEST
    if not manifest.is_file():
        raise ValueError("MULTIPLATE: missing current combined project; run regeneration")
    record = json.loads(manifest.read_bytes())
    if record.get("schema") != "native-combined-project-v1" or record.get("prepared_sha256") != M.sha(prepared):
        raise ValueError("MULTIPLATE: stale combined manifest")
    required = {"prepared_project": "prepared.3mf", "sliced_project": name,
                "native_receipt": "result.json", "invocation": "invocation.json"}
    payloads = {}
    for key, filename in required.items():
        pin = record[key]
        if pin["path"] != (directory / filename).relative_to(Path(root).resolve()).as_posix():
            raise ValueError("MULTIPLATE: unexpected cache binding")
        payloads[key] = read_bound(root, pin)
        if pin.get("bytes") != len(payloads[key]):
            raise ValueError("MULTIPLATE: cache byte count differs")
    if payloads["prepared_project"] != prepared:
        raise ValueError("MULTIPLATE: prepared input differs")
    proof = validate_output(prepared, payloads["sliced_project"], payloads["native_receipt"])
    if proof != record.get("verification"):
        raise ValueError("MULTIPLATE: cached verification differs")
    return dict(record, manifest=_pin(root, manifest))


def seal(root, plan, prepared, sliced, receipt, invocation):
    """Import a native run only after comparing it with the current prepared bytes."""
    expected = M.prepare(inputs(root, plan))
    if prepared != expected:
        raise ValueError("MULTIPLATE: native run used different inputs")
    proof = validate_output(prepared, sliced, receipt)
    directory, name = location(root, plan, prepared)
    if directory.exists():
        existing = current(root, plan, prepared=prepared)
        if existing["sliced_project"]["sha256"] != M.sha(sliced):
            raise ValueError("MULTIPLATE: immutable cache already contains another native run")
        return existing
    directory.mkdir(parents=True)
    payloads = {"prepared_project": ("prepared.3mf", prepared),
                "sliced_project": (name, sliced), "native_receipt": ("result.json", receipt),
                "invocation": ("invocation.json", _json(invocation))}
    record = dict(schema="native-combined-project-v1", prepared_sha256=M.sha(prepared),
                  verification=proof)
    for key, (filename, raw) in payloads.items():
        target = directory / filename
        with target.open("xb") as handle:
            handle.write(raw)
        record[key] = _pin(root, target)
    with (directory / MANIFEST).open("xb") as handle:
        handle.write(_json(record))
    return current(root, plan, prepared=prepared)


def native_slice(prepared, work, executable):
    executable = executable or os.environ.get("BAMBU_STUDIO") or shutil.which("bambu-studio")
    if not executable or not Path(executable).is_file():
        raise ValueError("MULTIPLATE: native Bambu Studio is required; set BAMBU_STUDIO")
    command = [str(executable), "--datadir", str(work / "data"),
               "--arrange", "0", "--orient", "0", "--slice", "0", "--debug", "2",
               "--outputdir", str(work), "--export-3mf", "sliced.3mf", str(prepared)]
    invocation = dict(command=command, executable_sha256=M.sha(Path(executable).read_bytes()))
    with (work / "stdout.log").open("wb") as log:
        result = subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT,
            timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError(f"MULTIPLATE: native slicer exit {result.returncode}; {work / 'stdout.log'}")
    return work / "sliced.3mf", work / "result.json", invocation


def ensure(root, plan, *, runner=None):
    prepared = M.prepare(inputs(root, plan))
    directory, _ = location(root, plan, prepared)
    if directory.exists():
        return current(root, plan, prepared=prepared)
    # Each run has its own data directory and logs, including failed runs.
    work = Path(tempfile.mkdtemp(prefix="combined-print-")).resolve()
    source = work / "prepared.3mf"
    source.write_bytes(prepared)
    sliced, receipt, invocation = (runner or native_slice)(
        source, work, plan["combined_project"].get("slicer"))
    return seal(root, plan, prepared, sliced.read_bytes(), receipt.read_bytes(), invocation)
