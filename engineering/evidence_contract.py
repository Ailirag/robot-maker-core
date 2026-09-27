"""Evidence provenance and applicability, without interpreting physical verdicts.

The caller supplies the current input contract independently of the record.
Hashes prove agreement with that declared closure; its completeness and the
meaning of an experiment belong to the producer and authoritative validator.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from engineering import build_composition as C
from engineering.instruction_inputs import value_digest

SCHEMA = 'engineering-evidence-v1'
RECHECKS = {
    'geometry': 'B-Rep, interfaces, clearances, insertion and instruction geometry',
    'electrical': 'netlist, board placement, harness and electrical bench checks',
    'firmware': 'compile, pin map, boot/fail-off behavior and measurement software',
    'composition': 'assembly integration, installed revisions and associated evidence',
    'conditions': 'repeat the experiment under the declared conditions',
}
CONTRACT_FIELDS = {'kind', 'procedure', 'subject', 'scope', 'inputs', 'consumed_facts',
                   'facts_not_applicable', 'runtime_names', 'outputs', 'validator'}
RECORD_FIELDS = {'schema', 'contract', 'procedure_sources', 'inputs', 'facts', 'outputs',
                 'validator', 'runtime', 'executor', 'recorded_at', 'result',
                 'physical_approval', 'identity_sha256'}


def _object(value, fields, label):
    if not isinstance(value, dict) or (fields is not None and set(value) != set(fields)):
        raise ValueError(f'{label}: missing or unknown fields')
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}: nonempty text is required')
    return value


def _reason(value, label):
    if len(_text(value, label).strip()) < 10:
        raise ValueError(f'{label}: explain why this is not applicable')


def _relation(value, label, required=False):
    _object(value, {'value', 'not_applicable'}, label)
    if value['value'] is None:
        if required:
            raise ValueError(f'{label}: a concrete identity is required')
        _reason(value['not_applicable'], label)
    else:
        _text(value['value'], label)
        if value['not_applicable'] is not None:
            raise ValueError(f'{label}: present and not-applicable contradict one another')


def _pins(root, specs, label, required=True):
    if not isinstance(specs, list) or (required and not specs):
        raise ValueError(f'{label}: declare a nonempty file closure')
    pins = []
    for spec in specs:
        _object(spec, {'path', 'hash_basis'}, label)
        pins.append(C.file_pin(root, spec['path'], spec['hash_basis']))
    if len({p['path'] for p in pins}) != len(pins):
        raise ValueError(f'{label}: duplicate file path')
    return sorted(pins, key=lambda p: p['path'])


def _snapshot(root, contract):
    _object(contract, CONTRACT_FIELDS, 'EVIDENCE_CONTRACT')
    kind = contract['kind']
    if kind not in ('geometry', 'physical', 'software'):
        raise ValueError('KIND: unsupported evidence kind')
    procedure = _object(contract['procedure'], {'id', 'revision', 'sources'}, 'PROCEDURE')
    _text(procedure['id'], 'PROCEDURE_ID')
    _text(procedure['revision'], 'PROCEDURE_REVISION')
    source_pins = _pins(root, procedure['sources'], 'PROCEDURE_SOURCES')
    subject = _object(contract['subject'], {'design_id', 'candidate', 'assembly', 'instance'}, 'SUBJECT')
    _text(subject['design_id'], 'DESIGN_ID')
    for key in ('candidate', 'assembly', 'instance'):
        _relation(subject[key], key, required=(kind == 'geometry' and key == 'candidate')
                  or (kind == 'physical' and key in ('assembly', 'instance')))
    if not _object(contract['scope'], None, 'SCOPE'):
        raise ValueError('SCOPE: an explicit applicability statement is required')
    domains = _object(contract['inputs'], RECHECKS, 'INPUT_DOMAINS')
    inputs = {}
    for domain, declaration in domains.items():
        _object(declaration, {'files', 'not_applicable'}, domain)
        inputs[domain] = _pins(root, declaration['files'], domain, required=False)
        if inputs[domain]:
            if declaration['not_applicable'] is not None:
                raise ValueError(f'{domain}: consumed and not-applicable contradict one another')
        else:
            if domain == 'geometry' and kind == 'geometry':
                raise ValueError('geometry: geometric evidence requires geometry inputs')
            _reason(declaration['not_applicable'], domain)
    consumed = contract['consumed_facts']
    if not isinstance(consumed, list):
        raise ValueError('FACTS: expected a declared list')
    facts = [C.capture_facts(root, entry) for entry in consumed]
    if len({f['path'] for f in facts}) != len(facts):
        raise ValueError('FACTS: duplicate manifest')
    if not facts:
        _reason(contract['facts_not_applicable'], 'FACTS')
    elif contract['facts_not_applicable'] is not None:
        raise ValueError('FACTS: consumed and not-applicable contradict one another')
    names = contract['runtime_names']
    if (not isinstance(names, list) or not names or any(not isinstance(n, str) or not n for n in names)
            or len(set(names)) != len(names)):
        raise ValueError('RUNTIME: declare unique runtime names')
    validator = _object(contract['validator'], {'source', 'decision', 'not_applicable'}, 'VALIDATOR')
    authority = {'source': _pins(root, [validator['source']], 'VALIDATOR_SOURCE')[0], 'decision': None}
    if validator['decision'] is None:
        if kind == 'physical':
            raise ValueError('VALIDATOR: physical evidence requires a linked decision')
        _reason(validator['not_applicable'], 'VALIDATOR_DECISION')
    else:
        if validator['not_applicable'] is not None:
            raise ValueError('VALIDATOR: decision and not-applicable contradict one another')
        authority['decision'] = _pins(root, [validator['decision']], 'VALIDATOR_DECISION')[0]
    return dict(procedure_sources=source_pins, inputs=inputs,
                facts=sorted(facts, key=lambda f: f['path']),
                outputs=_pins(root, contract['outputs'], 'OUTPUTS'), validator=authority)


def capture(root: Path, contract: dict, *, result: dict, runtime: dict,
            executor: str, recorded_at: str) -> dict:
    """Record a producer's original result, including a failure, without approval."""
    root = Path(root)
    snapshot = _snapshot(root, contract)
    _object(runtime, contract['runtime_names'], 'RUNTIME')
    for name, version in runtime.items():
        _text(version, f'RUNTIME {name}')
    _text(executor, 'EXECUTOR')
    _text(recorded_at, 'RECORDED_AT')
    if datetime.fromisoformat(recorded_at).tzinfo is None:
        raise ValueError('RECORDED_AT: timezone is required')
    if not _object(result, None, 'RAW_RESULT'):
        raise ValueError('RAW_RESULT: the original nonempty result is required')
    record = dict(schema=SCHEMA, contract=contract, **snapshot, runtime=runtime,
                  executor=executor, recorded_at=recorded_at, result=result, physical_approval=False)
    record['identity_sha256'] = value_digest(record)
    return json.loads(json.dumps(record, ensure_ascii=False, allow_nan=False))


def verify(root: Path, contract: dict, record: dict, *, raw_result: dict | None = None) -> list[str]:
    """Check against independently declared current scope/closure and raw result."""
    try:
        _object(record, RECORD_FIELDS, 'EVIDENCE_RECORD')
        if record['schema'] != SCHEMA or record['physical_approval'] is not False:
            raise ValueError('EVIDENCE_SCHEMA: unsupported schema or physical approval')
        if value_digest(record['contract']) != value_digest(contract):
            raise ValueError('APPLICABILITY: procedure, subject, scope or input contract changed')
        digest = value_digest({k:v for k,v in record.items() if k != 'identity_sha256'})
        if record['identity_sha256'] != digest:
            raise ValueError('EVIDENCE_IDENTITY: record content changed')
        if raw_result is not None and value_digest(record['result']) != value_digest(raw_result):
            raise ValueError('RAW_RESULT: receipt is attached to another result')
        current = capture(root, contract, result=record['result'], runtime=record['runtime'],
                          executor=record['executor'], recorded_at=record['recorded_at'])
        problems = []
        _object(record['inputs'], RECHECKS, 'INPUT_DOMAINS')
        for domain in RECHECKS:
            if value_digest(record['inputs'][domain]) != value_digest(current['inputs'][domain]):
                problems.append(f'RECHECK {domain}: {RECHECKS[domain]}')
        for field in ('procedure_sources', 'facts', 'outputs', 'validator'):
            if value_digest(record[field]) != value_digest(current[field]):
                problems.append(f'RECHECK {field}: evidence must be reproduced or reassessed by its authority')
        return problems
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return [f'EVIDENCE_INVALID: {exc}']


def legacy_level(root: Path, data: dict) -> str:
    """Classify historical records without inventing dates, runtime or approval."""
    sources = data.get('geometry_sources') if isinstance(data, dict) else None
    if not isinstance(sources, dict) or not sources:
        return 'LEGACY_UNVERIFIED'
    try:
        for path, digest in sources.items():
            if C.file_pin(Path(root), path)['sha256'] != digest:
                return 'LEGACY_UNVERIFIED'
    except (ValueError, TypeError, OSError):
        return 'LEGACY_UNVERIFIED'
    return 'LEGACY_SOURCE_HASHES_ONLY'
