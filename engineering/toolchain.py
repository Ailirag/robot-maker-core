"""Explicit runtime contracts; readiness never authorizes physical actions."""
from __future__ import annotations

from pathlib import Path
import json
import os
import re
import subprocess

from engineering import build_composition as C

SCHEMA = 'engineering-toolchain-v1'
FIELDS = {'schema', 'id', 'python', 'platform', 'machine', 'requirements', 'contrib_requirements',
          'freecad', 'sdk_packages', 'source_lf'}
OPTIONAL_FIELDS = {'max_sdk_root_chars'}


def package_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def parse_lock(text):
    """Accept target-specific exact versions with SHA-256 wheel/source pins."""
    entries, pending = {}, ''
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        pending += ' ' + line.removesuffix('\\').rstrip()
        if line.endswith('\\'):
            continue
        match = re.fullmatch(r'\s*([A-Za-z0-9_.-]+)==([A-Za-z0-9.+!-]+)((?:\s+--hash=sha256:[a-f0-9]{64})+)', pending)
        if not match:
            raise ValueError('LOCK: require exact versions and SHA-256 hashes, without editable/URL/marker inputs')
        name, version = package_name(match[1]), match[2]
        if name in entries:
            raise ValueError('LOCK: duplicate package ' + name)
        entries[name] = version
        pending = ''
    if pending or not entries:
        raise ValueError('LOCK: empty or unfinished requirement')
    return entries


def new_prefix(path):
    path = Path(path).resolve()
    if path.exists():
        raise ValueError('EXISTS: never adopt or replace an existing environment directory')
    path.mkdir(parents=True, exist_ok=False)
    return path


def locked_packages(root, pin):
    if C.file_pin(root, pin['path'], 'raw') != pin:
        raise ValueError('LOCK_DRIFT: requirements bytes changed')
    return parse_lock(C._path(root, pin['path']).read_text(encoding='utf-8'))


def clean_environment():
    if os.environ.get('PYTHONPATH') or os.environ.get('PYTHONHOME'):
        raise ValueError('PYTHONPATH/PYTHONHOME: clear inherited import overrides first')
    # Package installation must not inherit private indexes or hidden constraints.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(('PIP_', 'PLATFORMIO_'))}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_NO_CACHE_DIR='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    return env


def bootstrap(root, policy, prefix, *, python):
    """Create a fresh venv from hash-checked packages; never alter an old one."""
    root = Path(root).resolve()
    env = clean_environment()
    probe = subprocess.run([str(python), '-I', '-c', 'import sys; print(".".join(map(str,sys.version_info[:3])))'],
        env=env, capture_output=True, text=True, encoding='utf-8', check=True)
    if probe.stdout.strip() != policy['python']:
        raise ValueError('PYTHON_VERSION: bootstrap interpreter differs from the supported version')
    locked_packages(root, policy['requirements'])
    locked_packages(root, policy['contrib_requirements'])
    prefix = new_prefix(prefix)
    venv = prefix / 'venv'
    commands = [[str(python), '-I', '-m', 'venv', str(venv)]]
    executable = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    commands += [[str(executable), '-I', '-m', 'pip', 'install', '--index-url', 'https://pypi.org/simple',
                  '--require-hashes', '--only-binary=:all:', '-r', str(root / policy['requirements']['path'])],
                 [str(executable), '-I', '-m', 'pip', 'check']]
    log = ''
    for command in commands:
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
        log += result.stdout + result.stderr
        (prefix / 'BOOTSTRAP.log').write_text(log, encoding='utf-8', newline='\n')
        if result.returncode:
            raise ValueError(f'BOOTSTRAP_FAILED: exit {result.returncode}; retained {prefix / "BOOTSTRAP.log"}')
    # A source change during installation cannot inherit a successful bootstrap.
    locked_packages(root, policy['requirements'])
    locked_packages(root, policy['contrib_requirements'])
    record = {'schema': 'engineering-bootstrap-v1', 'toolchain_id': policy['id'],
              'python': str(executable), 'requirements': policy['requirements'],
              'contrib_requirements': policy['contrib_requirements'], 'physical_approval': False}
    (prefix / 'BOOTSTRAP.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8', newline='\n')
    return record


def diagnose(root, policy, observed):
    root, problems = Path(root), []
    try:
        if (not isinstance(policy, dict) or not FIELDS <= set(policy) <= FIELDS | OPTIONAL_FIELDS or policy['schema'] != SCHEMA
                or not isinstance(policy['id'], str) or not policy['id'].strip()):
            raise ValueError('TOOLCHAIN_SCHEMA: unsupported contract')
        if not isinstance(observed, dict):
            raise ValueError('OBSERVATION: runtime observation required')
        for key in ('python', 'platform', 'machine'):
            if observed.get(key) != policy[key]:
                problems.append(f'{key.upper()}: expected {policy[key]}, found {observed.get(key)}')
        if observed.get('isolated') is not True:
            problems.append('ISOLATION: a dedicated virtual environment is required')
        if observed.get('pythonpath'):
            problems.append('PYTHONPATH: inherited imports are forbidden')
        packages = locked_packages(root, policy['requirements'])
        if observed.get('packages') != packages:
            problems.append('PACKAGES: missing, different or undeclared distribution versions')
        if observed.get('contrib_packages') != locked_packages(root, policy['contrib_requirements']):
            problems.append('CONTRIB_PACKAGES: nested tool Python dependency versions differ')
        expected_fc = policy['freecad']
        actual_fc = observed.get('freecad')
        if (not isinstance(expected_fc, dict) or set(expected_fc) != {'version', 'python', 'occ'}
                or not isinstance(actual_fc, dict)
                or any(actual_fc.get(key) != value for key, value in expected_fc.items())):
            problems.append('FREECAD: independent verification runtime is missing or incompatible')
        if not isinstance(policy['sdk_packages'], dict) or observed.get('sdk_packages') != policy['sdk_packages']:
            problems.append('SDK_PACKAGES: compiler/framework/tool dependency versions differ')
        if 'max_sdk_root_chars' in policy:
            limit, path = policy['max_sdk_root_chars'], observed.get('sdk_root')
            if type(limit) is not int or limit <= 0:
                raise ValueError('SDK_PATH: invalid supported path budget')
            if not isinstance(path, str) or not path or len(path) > limit:
                problems.append(f'SDK_PATH: use a fresh SDK root no longer than {limit} characters')
        if not isinstance(policy['source_lf'], list) or not policy['source_lf']:
            raise ValueError('EOL_POLICY: source paths are required')
        for name in policy['source_lf']:
            if b'\r' in C._path(root, name).read_bytes():
                problems.append('EOL: expected LF in ' + name)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        problems.append(str(exc))
    return {'status': 'BLOCKED' if problems else 'READY', 'problems': problems,
            'physical_approval': False}
