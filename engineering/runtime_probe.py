"""Read runtime identities in their own processes and package directories."""
from __future__ import annotations

import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import site
import subprocess
import sys
import tempfile

from engineering.toolchain import clean_environment, package_name


def freecad(executable):
    with tempfile.TemporaryDirectory(prefix='engineering-runtime-') as directory:
        report = Path(directory) / 'runtime.json'
        env = clean_environment()
        env['ENGINEERING_RUNTIME_REPORT'] = str(report)
        command = ("import os,sys,json,FreeCAD,Part; "
                   "r={'version':'.'.join(FreeCAD.Version()[:3]),"
                   "'python':'.'.join(map(str,sys.version_info[:3])),'occ':Part.OCC_VERSION}; "
                   "open(os.environ['ENGINEERING_RUNTIME_REPORT'],'w',encoding='utf-8').write(json.dumps(r))")
        try:
            result = subprocess.run([str(executable), '-c', command], env=env,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace')
            if result.returncode or not report.is_file():
                raise ValueError('FREECAD: runtime process failed or produced no report')
            data = json.loads(report.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or set(data) != {'version', 'python', 'occ'}
                    or any(not isinstance(v, str) or not v.strip() for v in data.values())):
                raise ValueError('FREECAD: malformed runtime identity')
            return data
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('FREECAD: ' + str(exc)) from exc


def sdk(root):
    result = {}
    for group, filename in (('platforms', 'platform.json'), ('packages', 'package.json')):
        parent = Path(root) / group
        if not parent.exists():
            continue
        for directory in sorted(parent.iterdir()):
            if not directory.is_dir() or directory.name.startswith('.'):
                continue
            try:
                data = json.loads((directory / filename).read_text(encoding='utf-8'))
                if (not isinstance(data, dict) or data.get('name') != directory.name
                        or not isinstance(data.get('version'), str) or not data['version']
                        or data['name'] in result):
                    raise ValueError('SDK: inconsistent or duplicate package identity')
                result[data['name']] = data['version']
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError('SDK: missing or malformed package manifest ' + str(directory)) from exc
    return result


def packages(path=None):
    result = {}
    for distribution in metadata.distributions(**({'path': [str(path)]} if path is not None else {})):
        name = package_name(distribution.metadata['Name'])
        if name in result:
            raise ValueError('PACKAGE_DUPLICATE: ' + name)
        result[name] = distribution.version
    return result


def observe(sdk_root, freecadcmd):
    config = Path(sys.prefix) / 'pyvenv.cfg'
    isolated = (sys.prefix != sys.base_prefix and not site.ENABLE_USER_SITE and config.is_file()
                and 'include-system-site-packages = false' in config.read_text(encoding='utf-8').lower())
    return {'python': platform.python_version(), 'platform': sys.platform, 'machine': platform.machine(),
            'sdk_root': str(Path(sdk_root).resolve()),
            'isolated': isolated, 'pythonpath': os.environ.get('PYTHONPATH') or os.environ.get('PYTHONHOME'),
            'packages': packages(), 'freecad': freecad(freecadcmd), 'sdk_packages': sdk(sdk_root),
            'contrib_packages': packages(Path(sdk_root) / 'packages/tool-esptoolpy/_contrib')}
