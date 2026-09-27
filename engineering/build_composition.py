"""Content-bound review compositions and write-once local bundle transactions.

The project declares the graph, semantic revisions, files and fact contracts.
Producers must supply and check their own build records before capture: a hash
graph proves consistency of declared inputs, not that a compiler or bench ran.
No operation in this module grants physical permission or uploads firmware.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

from engineering import fact_closure
from engineering.facts import FactManifest, FactStatus
from engineering.instruction_inputs import value_digest

SCHEMA = 'engineering-build-composition-v1'
KINDS = frozenset({'facts', 'geometry', 'electrical', 'pin_map', 'launch_stage',
                   'firmware', 'instruction', 'print_job', 'evidence'})
RECIPE_FIELDS = {'kind', 'revision', 'semantics', 'files', 'consumes', 'requires'}
DOCUMENT_FIELDS = {'schema', 'design_id', 'revision', 'status', 'physical_approval', 'nodes', 'identity_sha256'}
FACT_CONTRACT_FIELDS = {'expected_unit', 'allowed_provenance', 'require_datum', 'expected_scope'}
DEPENDENCY_KINDS = {
    'pin_map': {'electrical'}, 'launch_stage': {'electrical'},
    'firmware': {'pin_map', 'launch_stage'}, 'print_job': {'geometry'},
    'instruction': {'geometry', 'electrical', 'firmware', 'print_job'},
}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}: expected nonempty text')
    return value


def _mapping(value, label):
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
        raise ValueError(f'{label}: expected an object with nonempty keys')
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f'{label}: expected a list')
    return value


def _path(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or ':' in relative or '\\' in relative
            or any(p in {'', '.', '..'} for p in relative.split('/'))):
        raise ValueError(f'PATH: invalid relative path {relative!r}')
    root = root.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in [path, *path.parents] if p != root):
        raise ValueError(f'PATH: outside root or symlink {relative}')
    if not path.is_file():
        raise ValueError(f'FILE_MISSING: {relative}')
    return path


def file_pin(root: Path, relative: str, hash_basis: str = 'raw') -> dict:
    if hash_basis not in {'raw', 'canonical_lf'}:
        raise ValueError(f'HASH_BASIS: unsupported {hash_basis!r}')
    payload = _path(Path(root), relative).read_bytes()
    if hash_basis == 'canonical_lf':
        payload.decode('utf-8')
        payload = payload.replace(b'\r\n', b'\n')
    return {'path': relative, 'hash_basis': hash_basis, 'bytes': len(payload),
            'sha256': hashlib.sha256(payload).hexdigest()}


def capture_facts(root: Path, entry: dict) -> dict:
    """Capture explicitly consumed records using their declared fact contracts."""
    _mapping(entry, 'FACT_CLOSURE')
    if set(entry) not in ({'path', 'contracts'}, {'path', 'contracts', 'flags'}):
        raise ValueError('FACT_CLOSURE: expected path, contracts and optional flags')
    path = _path(root, entry['path'])
    contracts = _mapping(entry['contracts'], 'FACT_CONTRACTS')
    flags = _list(entry.get('flags', []), 'FACT_FLAGS')
    if any(not isinstance(f, str) or not f for f in flags) or len(set(flags)) != len(flags):
        raise ValueError('FACT_FLAGS: expected unique nonempty flag IDs')
    if not contracts and not flags:
        raise ValueError('FACT_CONTRACTS: consumed facts must be declared')
    manifest = FactManifest.from_path(path)
    table = fact_closure.fact_table(path.read_bytes())
    records = {}
    for fact_id, contract in sorted(contracts.items()):
        _mapping(contract, f'FACT_CONTRACT {fact_id}')
        if set(contract) != FACT_CONTRACT_FIELDS:
            raise ValueError(f'FACT_CONTRACT {fact_id}: declare unit, provenance, datum and scope requirements')
        _text(contract['expected_unit'], f'FACT_UNIT {fact_id}')
        allowed = _list(contract['allowed_provenance'], f'FACT_PROVENANCE {fact_id}')
        if not allowed or any(not isinstance(p, str) or not p for p in allowed):
            raise ValueError(f'FACT_PROVENANCE {fact_id}: declare allowed provenance')
        if type(contract['require_datum']) is not bool:
            raise ValueError(f'FACT_DATUM {fact_id}: must be boolean')
        if contract['expected_scope'] is not None:
            _text(contract['expected_scope'], f'FACT_SCOPE {fact_id}')
        result = manifest.resolve(fact_id, **contract)
        if result.status != FactStatus.READY:
            raise ValueError(f'FACT_UNAVAILABLE: {fact_id}: {result.status}: {result.detail}')
        records[fact_id] = fact_closure.fact_record(table, fact_id)
    flag_records = {}
    for flag in sorted(flags):
        record = manifest.acceptance_flag(flag)
        if record['state'] != 'recorded':
            raise ValueError(f'FACT_UNAVAILABLE: flag {flag}: {record["state"]}')
        flag_records[flag] = record
    return {'path': entry['path'], 'contracts': contracts, 'records': records, 'flags': flag_records}


def _order(nodes: dict) -> list[str]:
    _mapping(nodes, 'NODES')
    kinds, dependencies = set(), {}
    for key, node in nodes.items():
        _mapping(node, f'NODE {key}')
        if set(node) != RECIPE_FIELDS:
            raise ValueError(f'NODE {key}: expected fields {sorted(RECIPE_FIELDS)}')
        if node['kind'] not in KINDS:
            raise ValueError(f'KIND {key}: unknown kind')
        kinds.add(node['kind'])
        _text(node['revision'], f'REVISION {key}')
        if not _mapping(node['semantics'], f'SEMANTICS {key}'):
            raise ValueError(f'SEMANTICS {key}: declare the semantic composition')
        deps = _list(node['requires'], f'DEPENDENCIES {key}')
        if any(not isinstance(d, str) or d not in nodes for d in deps) or len(set(deps)) != len(deps):
            raise ValueError(f'DEPENDENCIES {key}: unknown or duplicate dependency')
        dependencies[key] = set(deps)
    if kinds != KINDS:
        raise ValueError(f'INCOMPLETE_COMPOSITION: missing kinds {sorted(KINDS - kinds)}')
    ordered = []
    while len(ordered) < len(nodes):
        ready = sorted(k for k, deps in dependencies.items() if k not in ordered and deps <= set(ordered))
        if not ready:
            raise ValueError('DEPENDENCY_CYCLE: composition must be acyclic')
        ordered.extend(ready)
    ancestors = {}
    for key in ordered:
        ancestors[key] = set(dependencies[key])
        for dependency in dependencies[key]:
            ancestors[key].update(ancestors[dependency])
        required = DEPENDENCY_KINDS.get(nodes[key]['kind'], set())
        present = {nodes[d]['kind'] for d in ancestors[key]}
        if not required <= present:
            raise ValueError(f'DEPENDENCY_KINDS {key}: missing {sorted(required - present)}')
    return ordered


def capture(root: Path, *, design_id: str, revision: str, nodes: dict) -> dict:
    """Capture declared current files/consumed facts after producer validation.

    This does not create or rebuild outputs. The caller must reject stale build
    records, incomplete source closures and mismatched project contracts first.
    """
    root = Path(root).resolve()
    _text(design_id, 'DESIGN_ID')
    _text(revision, 'REVISION')
    out = {}
    for key in _order(nodes):
        recipe = nodes[key]
        files = []
        for spec in _list(recipe['files'], f'FILES {key}'):
            if not isinstance(spec, dict) or set(spec) != {'path', 'hash_basis'}:
                raise ValueError(f'FILES {key}: expected path and hash_basis')
            files.append(file_pin(root, spec['path'], spec['hash_basis']))
        if len({f['path'] for f in files}) != len(files):
            raise ValueError(f'FILES {key}: duplicate path')
        consumed = [capture_facts(root, f) for f in _list(recipe['consumes'], f'FACTS {key}')]
        if recipe['kind'] == 'facts' and not consumed:
            raise ValueError(f'FACTS {key}: missing consumed fact closure')
        if not files and not consumed:
            raise ValueError(f'INPUTS {key}: no files or consumed facts')
        node = {'kind': recipe['kind'], 'revision': recipe['revision'], 'semantics': recipe['semantics'],
                'files': sorted(files, key=lambda f: f['path']),
                'consumes': sorted(consumed, key=lambda f: f['path']),
                'requires': {d: out[d]['identity_sha256'] for d in sorted(recipe['requires'])}}
        node['identity_sha256'] = value_digest(node)
        out[key] = node
    document = {'schema': SCHEMA, 'design_id': design_id, 'revision': revision, 'status': 'REVIEW_ONLY',
                'physical_approval': False, 'nodes': out}
    document['identity_sha256'] = value_digest(document)
    # Detach all dictionaries from producer-owned objects; enforce strict JSON.
    return json.loads(json.dumps(document, ensure_ascii=False, allow_nan=False))


def verify(root: Path, document: dict) -> list[str]:
    """Refuse drift and propagate changed inputs through the declared graph."""
    try:
        _mapping(document, 'COMPOSITION')
        if set(document) != DOCUMENT_FIELDS or document['schema'] != SCHEMA:
            raise ValueError('COMPOSITION_SCHEMA: unsupported fields or schema')
        if document['status'] != 'REVIEW_ONLY' or document['physical_approval'] is not False:
            raise ValueError('NO_PHYSICAL_APPROVAL: a composition is review evidence only')
        if document['identity_sha256'] != value_digest({k: v for k, v in document.items() if k != 'identity_sha256'}):
            raise ValueError('COMPOSITION_IDENTITY: digest mismatch')
        recipes = {}
        for key, node in _mapping(document['nodes'], 'NODES').items():
            if not isinstance(node, dict) or set(node) != RECIPE_FIELDS | {'identity_sha256'}:
                raise ValueError(f'NODE_SCHEMA {key}')
            if node['identity_sha256'] != value_digest({k: v for k, v in node.items() if k != 'identity_sha256'}):
                raise ValueError(f'NODE_IDENTITY {key}')
            _mapping(node['requires'], f'DEPENDENCIES {key}')
            for dep, digest in node['requires'].items():
                if dep not in document['nodes'] or digest != document['nodes'][dep]['identity_sha256']:
                    raise ValueError(f'DEPENDENCY_PIN {key}: {dep}')
            specs = []
            for pin in _list(node['files'], f'FILES {key}'):
                if not isinstance(pin, dict) or set(pin) != {'path', 'hash_basis', 'bytes', 'sha256'}:
                    raise ValueError(f'FILE_PIN {key}: incomplete pin')
                if type(pin['bytes']) is not int or pin['bytes'] < 0:
                    raise ValueError(f'FILE_PIN {key}: bytes must be a nonnegative integer')
                digest = pin['sha256']
                if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                    raise ValueError(f'FILE_PIN {key}: invalid sha256')
                specs.append({'path': pin['path'], 'hash_basis': pin['hash_basis']})
            facts = []
            for f in _list(node['consumes'], f'FACTS {key}'):
                if not isinstance(f, dict) or set(f) != {'path', 'contracts', 'records', 'flags'}:
                    raise ValueError(f'FACT_PIN {key}: incomplete closure')
                _mapping(f['flags'], f'FACT_FLAGS {key}')
                facts.append({'path': f['path'], 'contracts': f['contracts'], 'flags': list(f['flags'])})
            recipes[key] = {k: node[k] for k in ('kind', 'revision', 'semantics')}
            recipes[key].update(files=specs, consumes=facts, requires=list(node['requires']))
        current = capture(root, design_id=document['design_id'], revision=document['revision'], nodes=recipes)
        problems = []
        for key in _order(recipes):
            old, new = document['nodes'][key], current['nodes'][key]
            if value_digest(old['files']) != value_digest(new['files']):
                problems.append(f'FILE_DRIFT {key}: a declared file changed')
            if value_digest(old['consumes']) != value_digest(new['consumes']):
                problems.append(f'FACT_DRIFT {key}: a consumed fact changed')
            if old['requires'] != new['requires']:
                problems.append(f'DEPENDENCY_DRIFT {key}: regenerate its dependent outputs and evidence')
        return problems
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return [f'COMPOSITION_INVALID: {exc}']


def _write_json(path: Path, value: dict):
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _new_directory(path: Path):
    try:
        path.mkdir(parents=False, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f'EXISTS: never overwrite a prepared or published bundle: {path}') from exc


def _input_paths(document: dict) -> list[str]:
    return sorted({pin['path'] for node in document['nodes'].values()
                   for pin in node['files'] + node['consumes']})


def prepare(root: Path, document: dict, staging: Path):
    """Prepare a verified independent copy in a new directory, without a marker."""
    root, staging = Path(root).resolve(), Path(staging).resolve()
    problems = verify(root, document)
    if problems:
        raise ValueError('\n'.join(problems))
    _new_directory(staging)
    for relative in _input_paths(document):
        target = staging / 'files' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_path(root, relative), target)
    problems = verify(staging / 'files', document)
    if problems:
        raise ValueError('\n'.join(problems))
    _write_json(staging / 'COMPOSITION.json', document)


def _read_prepared(directory: Path) -> dict:
    if directory.is_symlink():
        raise ValueError('BUNDLE_PATH: symlink')
    document = json.loads(_path(directory, 'COMPOSITION.json').read_text(encoding='utf-8'))
    problems = verify(directory / 'files', document)
    expected = {'files/' + p for p in _input_paths(document)} | {'COMPOSITION.json'}
    paths = list(directory.rglob('*'))
    if any(p.is_symlink() for p in paths):
        problems.append('BUNDLE_PATH: symlink')
    actual = {p.relative_to(directory).as_posix() for p in paths if p.is_file()} - {'COMPLETE.json'}
    if expected != actual:
        problems.append('BUNDLE_CLOSURE: missing or undeclared files')
    if problems:
        raise ValueError('\n'.join(problems))
    return document


def publish(staging: Path, destination: Path):
    """Publish locally with a last-written completeness marker; never overwrite.

    Interrupted copies stay visibly incomplete. Consumers must use verify_bundle,
    never directory existence, as the transaction completion test. Original
    staging is retained; retry requires another new destination.
    """
    staging, destination = Path(staging).resolve(), Path(destination).resolve()
    document = _read_prepared(staging)
    _new_directory(destination)
    for relative in ['COMPOSITION.json', *['files/' + p for p in _input_paths(document)]]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_path(staging, relative), target)
        with target.open('r+b') as stream:
            os.fsync(stream.fileno())
    copied = _read_prepared(destination)
    if copied != document:
        raise ValueError('BUNDLE_CHANGED: staged composition changed during copy')
    _write_json(destination / 'COMPLETE.json', {
        'schema': 'engineering-bundle-complete-v1', 'identity_sha256': document['identity_sha256'],
        'composition': file_pin(destination, 'COMPOSITION.json')})


def verify_bundle(directory: Path) -> list[str]:
    try:
        directory = Path(directory)
        marker = json.loads(_path(directory, 'COMPLETE.json').read_text(encoding='utf-8'))
        document = _read_prepared(directory)
        if marker != {'schema': 'engineering-bundle-complete-v1', 'identity_sha256': document['identity_sha256'],
                       'composition': file_pin(directory, 'COMPOSITION.json')}:
            raise ValueError('BUNDLE_MARKER: composition does not match the completion marker')
        return []
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return [f'BUNDLE_INVALID: {exc}']
