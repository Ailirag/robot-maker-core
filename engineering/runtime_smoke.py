"""Synthetic build123d -> STEP -> FreeCAD exchange, never robot geometry.

The 10 x 20 x 30 mm box and 2 mm bore are declared test constants. They are
not measurements, component dimensions, a prototype revision or print approval.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import math
from pathlib import Path
import subprocess
import sys

from engineering.toolchain import clean_environment, new_prefix


def problems(actual):
    errors = []
    if not isinstance(actual, dict):
        return ['SMOKE: missing geometry observations']
    for key in ('valid', 'axis_is_empty', 'wall_is_material'):
        if actual.get(key) is not True:
            errors.append('SMOKE: ' + key)
    if type(actual.get('solids')) is not int or actual['solids'] != 1:
        errors.append('SMOKE: expected one solid')
    try:
        volume = actual['volume_mm3']
        if type(volume) not in (int, float) or not math.isclose(volume, 6000-120*math.pi, rel_tol=1e-9, abs_tol=1e-6):
            errors.append('SMOKE: analytic volume differs')
        box = actual['bbox_mm']
        if not isinstance(box, list) or len(box) != 3 or any(
                type(a) not in (int, float) or not math.isclose(a,b,rel_tol=0,abs_tol=1e-7) for a,b in zip(box,[10,20,30])):
            errors.append('SMOKE: bounding box differs')
    except (KeyError, TypeError, ValueError):
        errors.append('SMOKE: malformed numeric observations')
    return errors


def pin(path):
    data = path.read_bytes()
    return {'path': path.name, 'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}


def write(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, indent=2)+'\n')


def export(destination):
    destination = new_prefix(destination)
    from build123d import Box, Cylinder, export_step, export_stl
    shape = Box(10,20,30) - Cylinder(2,40)
    solid = shape.solids()[0]
    size = shape.bounding_box().size
    actual = {'valid':shape.is_valid, 'solids':len(shape.solids()), 'volume_mm3':shape.volume,
              'bbox_mm':[size.X,size.Y,size.Z],
              'axis_is_empty':all(not solid.is_inside((0,0,z)) for z in (-14,0,14)),
              'wall_is_material':all(solid.is_inside(point) for point in ((4,0,0),(0,9,0)))}
    if problems(actual):
        raise ValueError('; '.join(problems(actual)))
    export_step(shape, destination/'smoke.step')
    export_stl(shape, destination/'smoke.stl', tolerance=0.1, angular_tolerance=0.1)
    record = {'schema':'engineering-runtime-smoke-v1', 'scope':'synthetic-toolchain-fixture',
              'obtained_by':'Declared analytic test constants; no robot measurements.',
              'producer_sha256':pin(Path(__file__))['sha256'],
              'recorded_at':datetime.now(timezone.utc).isoformat(), 'python':sys.version,
              'packages':{name:metadata.version(name) for name in ('build123d','cadquery-ocp-novtk')},
              'geometry':actual, 'outputs':[pin(destination/name) for name in ('smoke.step','smoke.stl')],
              'physical_approval':False}
    write(destination/'BUILD123D.json',record)
    return record


def exchange(destination, freecadcmd):
    destination = Path(destination).resolve()
    record = json.loads((destination/'BUILD123D.json').read_text(encoding='utf-8'))
    if (destination/'FREECAD.json').exists() or (destination/'EXCHANGE.json').exists():
        raise ValueError('EXISTS: exchange output is immutable; use another fresh run')
    if record['producer_sha256'] != pin(Path(__file__))['sha256']:
        raise ValueError('SMOKE: procedure changed after export')
    for output in record['outputs']:
        if output != pin(destination/output['path']):
            raise ValueError('SMOKE: exported artifact changed')
    env = clean_environment()
    env['ENGINEERING_SMOKE_DIR'] = str(destination)
    code = (
        "import os,json,Part,FreeCAD; from pathlib import Path; "
        "d=Path(os.environ['ENGINEERING_SMOKE_DIR']); s=Part.read(str(d/'smoke.step')); b=s.BoundBox; "
        "r={'valid':s.isValid(),'solids':len(s.Solids),'volume_mm3':s.Volume,"
        "'bbox_mm':[b.XLength,b.YLength,b.ZLength],"
        "'axis_is_empty':all(not s.isInside(FreeCAD.Vector(0,0,z),1e-7,True) for z in (-14,0,14)),"
        "'wall_is_material':all(s.isInside(FreeCAD.Vector(*p),1e-7,True) for p in ((4,0,0),(0,9,0)))}; "
        "f=(d/'FREECAD.json').open('x',encoding='utf-8'); json.dump(r,f,indent=2); f.close()")
    result = subprocess.run([str(freecadcmd),'-c',code],env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
    with (destination/'FREECAD.log').open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(result.stdout+result.stderr)
    if result.returncode or not (destination/'FREECAD.json').is_file():
        raise ValueError('SMOKE: independent reopen failed')
    actual = json.loads((destination/'FREECAD.json').read_text(encoding='utf-8'))
    errors = problems(actual)
    report = {'schema':'engineering-runtime-exchange-v1','status':'FAIL' if errors else 'PASS',
              'errors':errors,'inputs':[pin(destination/name) for name in ('BUILD123D.json','smoke.step','smoke.stl')],
              'outputs':[pin(destination/name) for name in ('FREECAD.json','FREECAD.log')],
              'physical_approval':False}
    write(destination/'EXCHANGE.json',report)
    if errors:
        raise ValueError('; '.join(errors))
    return report
