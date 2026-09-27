"""PlatformIO compile-only producer receipts; build contracts are project data.

Declared inputs are hashed before and after compilation. Successful artifacts
are copied to a new directory and pinned together with the tool's output log.
The receipt is written last. An existing destination is never reused.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from engineering import build_composition as C
from engineering.instruction_inputs import value_digest

SCHEMA = 'engineering-firmware-build-v1'
CONTRACT_FIELDS = {'build_id', 'project_dir', 'environment', 'sources', 'outputs', 'excluded_local_inputs'}
OPTIONAL_CONTRACT_FIELDS = {'source_date_epoch', 'runtime_policy'}
_OBSERVE_RUNTIME = object()
RECORD_FIELDS = {'schema', 'contract', 'tool_version', 'recorded_at', 'exit_code', 'inputs', 'outputs',
                 'log', 'producer', 'physical_approval', 'identity_sha256'}


def _producer_sources() -> list[Path]:
    from engineering import instruction_inputs, facts, fact_closure, toolchain, runtime_probe
    return [Path(m.__file__) for m in (sys.modules[__name__], C, instruction_inputs, facts, fact_closure, toolchain, runtime_probe)]


def _relative(root: Path, text: str) -> Path:
    if (not isinstance(text, str) or not text or ':' in text or '\\' in text
            or any(p in {'', '.', '..'} for p in text.split('/'))):
        raise ValueError(f'CONTRACT_PATH: {text!r}')
    path = root / text
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'CONTRACT_PATH: outside root: {text}')
    return path


def _validate_contract(root: Path, contract: dict):
    if not isinstance(contract, dict) or not CONTRACT_FIELDS <= set(contract) <= CONTRACT_FIELDS | OPTIONAL_CONTRACT_FIELDS:
        raise ValueError('BUILD_CONTRACT: incomplete or unknown fields')
    if 'source_date_epoch' in contract and (type(contract['source_date_epoch']) is not int or contract['source_date_epoch'] < 0):
        raise ValueError('BUILD_CONTRACT: source_date_epoch must be a nonnegative integer')
    for field in ('build_id', 'environment'):
        if not isinstance(contract[field], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', contract[field]):
            raise ValueError(f'BUILD_CONTRACT: invalid {field}')
    project = _relative(root, contract['project_dir'])
    if not project.is_dir():
        raise ValueError('BUILD_PROJECT: missing project directory')
    for field in ('sources', 'outputs', 'excluded_local_inputs'):
        values = contract[field]
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values) or len(values) != len(set(values)):
            raise ValueError(f'BUILD_CONTRACT: invalid {field}')
        for relative in values:
            _relative(root, relative)
    if not contract['sources'] or not contract['outputs']:
        raise ValueError('BUILD_CONTRACT: sources and outputs are required')
    if contract['project_dir'] + '/platformio.ini' not in contract['sources']:
        raise ValueError('BUILD_CONTRACT: the effective project configuration must be an input')
    if 'runtime_policy' in contract and contract['runtime_policy'] not in contract['sources']:
        raise ValueError('BUILD_CONTRACT: runtime policy must be a declared source')
    for name in contract['outputs']:
        if '/' in name or name in {'BUILD.json', 'BUILD.log'}:
            raise ValueError('BUILD_CONTRACT: outputs must be distinct artifact basenames')
    for relative in contract['excluded_local_inputs']:
        if _relative(root, relative).exists():
            raise ValueError(f'LOCAL_INPUT: review build excludes {relative}')


def _inputs(root: Path, contract: dict) -> list:
    return [C.file_pin(root, path, 'canonical_lf') for path in sorted(contract['sources'])]


def _environment(contract):
    env = dict(os.environ)
    forbidden = {'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'CPATH', 'CPLUS_INCLUDE_PATH',
                 'C_INCLUDE_PATH', 'LIBRARY_PATH', 'COMPILER_PATH', 'GCC_EXEC_PREFIX',
                 'PYTHONPATH', 'PYTHONHOME'}
    for name, value in env.items():
        if value and (name in forbidden or (name.startswith('PLATFORMIO_') and name != 'PLATFORMIO_CORE_DIR')):
            raise ValueError('BUILD_ENVIRONMENT: undeclared override ' + name)
    if 'source_date_epoch' in contract:
        env['SOURCE_DATE_EPOCH'] = str(contract['source_date_epoch'])
    elif env.get('SOURCE_DATE_EPOCH'):
        raise ValueError('BUILD_ENVIRONMENT: SOURCE_DATE_EPOCH must be declared in the contract')
    env.pop('PYTHONPATH', None)
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def _runtime_policy(root, contract, observed=_OBSERVE_RUNTIME):
    if 'runtime_policy' not in contract:
        return None
    from engineering import runtime_probe, toolchain
    policy = json.loads(_relative(root, contract['runtime_policy']).read_text(encoding='utf-8'))
    if observed is _OBSERVE_RUNTIME:
        sdk, freecad = os.environ.get('PLATFORMIO_CORE_DIR'), os.environ.get('FREECADCMD')
        if not sdk or not freecad:
            raise ValueError('BUILD_RUNTIME: explicit SDK and FreeCAD locations are required')
        observed = runtime_probe.observe(sdk, freecad)
    result = toolchain.diagnose(root, policy, observed)
    if result['status'] != 'READY':
        raise ValueError('BUILD_RUNTIME: ' + '; '.join(result['problems']))
    return observed


def _tool_version(runtime, version):
    if runtime is not None:
        expected = runtime.get('packages', {}).get('platformio')
        if not expected or version != 'PlatformIO Core, version ' + expected:
            raise ValueError('BUILD_TOOL: executable version differs from the declared runtime')


def build(root: Path, contract: dict, *, executable: str, destination: Path) -> dict:
    root, destination = Path(root).resolve(), Path(destination).resolve()
    _validate_contract(root, contract)
    env = _environment(contract)
    if not destination.is_relative_to(root):
        raise ValueError('BUILD_DESTINATION: keep receipts inside the declared repository root')
    if destination.exists():
        raise ValueError(f'EXISTS: build destination already exists: {destination}')
    runtime = _runtime_policy(root, contract)
    before = _inputs(root, contract)
    C._new_directory(destination)
    producer_images = {path: path.read_bytes() for path in _producer_sources()}
    producer_pins = []
    (destination / 'producer').mkdir()
    for source, payload in producer_images.items():
        target = destination / 'producer' / source.name
        with target.open('xb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        producer_pins.append(C.file_pin(root, target.relative_to(root).as_posix()))
    options = dict(cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    version = subprocess.run([str(executable), '--version'], **options)
    if version.returncode != 0 or not version.stdout.strip():
        raise ValueError('BUILD_TOOL: could not identify PlatformIO')
    _tool_version(runtime, version.stdout.strip())
    command = [str(executable), 'run', '--project-dir', str(root / contract['project_dir']), '-e', contract['environment']]
    result = subprocess.run(command, **options)
    log_path = destination / 'BUILD.log'
    with log_path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(result.stdout + result.stderr)
        stream.flush()
        os.fsync(stream.fileno())
    if result.returncode != 0:
        raise ValueError(f'BUILD_FAILED: exit {result.returncode}; log {log_path}')
    if _inputs(root, contract) != before:
        raise ValueError('INPUT_DRIFT: sources changed during compilation')
    if any(path.read_bytes() != payload for path, payload in producer_images.items()):
        raise ValueError('PRODUCER_DRIFT: procedure changed during compilation')
    if _runtime_policy(root, contract) != runtime:
        raise ValueError('BUILD_RUNTIME: runtime changed during compilation')
    outputs = []
    for name in contract['outputs']:
        relative = f"{contract['project_dir']}/.pio/build/{contract['environment']}/{name}"
        source = C._path(root, relative)
        target = destination / name
        shutil.copyfile(source, target)
        with target.open('r+b') as stream:
            os.fsync(stream.fileno())
        source_pin = C.file_pin(root, relative)
        pin = C.file_pin(root, target.relative_to(root).as_posix())
        if (source_pin['bytes'], source_pin['sha256']) != (pin['bytes'], pin['sha256']):
            raise ValueError('ARTIFACT_DRIFT: compiler output changed during copy')
        outputs.append(pin)
    record = {'schema': SCHEMA, 'contract': contract, 'tool_version': version.stdout.strip(),
              'recorded_at': datetime.now(timezone.utc).isoformat(), 'exit_code': 0,
              'inputs': before, 'outputs': outputs,
              'producer': {'schema': 'engineering-firmware-producer-v1', 'python': sys.version,
                           'sources': producer_pins},
              'log': C.file_pin(root, log_path.relative_to(root).as_posix()), 'physical_approval': False}
    if runtime is not None:
        record['producer'].update(schema='engineering-firmware-producer-v2', runtime=runtime)
    record['identity_sha256'] = value_digest(record)
    problems = verify_record(root, contract, record)
    if problems:
        raise ValueError('\n'.join(problems))
    C._write_json(destination / 'BUILD.json', record)
    return record


def verify_record(root: Path, contract: dict, record: dict) -> list[str]:
    try:
        root = Path(root).resolve()
        _validate_contract(root, contract)
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS or record['schema'] != SCHEMA:
            raise ValueError('BUILD_RECORD: unsupported schema or fields')
        if record['contract'] != contract:
            raise ValueError('BUILD_CONTRACT_DRIFT: environment or source/output contract changed')
        if type(record['exit_code']) is not int or record['exit_code'] != 0 or record['physical_approval'] is not False:
            raise ValueError('BUILD_RECORD: not a successful compile-only receipt')
        if record['identity_sha256'] != value_digest({k: v for k, v in record.items() if k != 'identity_sha256'}):
            raise ValueError('BUILD_RECORD: identity mismatch')
        if not isinstance(record['tool_version'], str) or not record['tool_version'].strip():
            raise ValueError('BUILD_TOOL: no version')
        if datetime.fromisoformat(record['recorded_at']).tzinfo is None:
            raise ValueError('BUILD_TIME: timezone is required')
        if _inputs(root, contract) != record['inputs']:
            raise ValueError('INPUT_DRIFT: producer receipt belongs to other sources or configuration')
        if not isinstance(record['outputs'], list) or [Path(p['path']).name for p in record['outputs']] != contract['outputs']:
            raise ValueError('BUILD_OUTPUTS: incomplete artifact set')
        producer = record['producer']
        bound_runtime = 'runtime_policy' in contract
        producer_fields = {'schema', 'python', 'sources'} | ({'runtime'} if bound_runtime else set())
        producer_schema = 'engineering-firmware-producer-v2' if bound_runtime else 'engineering-firmware-producer-v1'
        if (not isinstance(producer, dict) or set(producer) != producer_fields
                or producer['schema'] != producer_schema
                or not isinstance(producer['python'], str) or not producer['python']
                or not isinstance(producer['sources'], list) or not producer['sources']):
            raise ValueError('BUILD_PRODUCER: missing procedure/runtime provenance')
        if bound_runtime:
            _runtime_policy(root, contract, producer['runtime'])
            _tool_version(producer['runtime'], record['tool_version'])
        for pin in record['outputs'] + [record['log']] + producer['sources']:
            if C.file_pin(root, pin['path'], 'raw') != pin:
                raise ValueError(f"ARTIFACT_DRIFT: {pin['path']}")
        return []
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return [f'BUILD_INVALID: {exc}']
