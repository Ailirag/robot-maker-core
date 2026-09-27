"""A data contract for a reproducible manual; never a physical gate interpreter."""
from __future__ import annotations

import math
import re

from engineering.instruction_inputs import value_digest

SCHEMA = 'assembly-manual-v1'
DOCUMENT = {'schema', 'scope', 'stages', 'parts', 'measurements', 'operations'}
OPERATION = {'id', 'title', 'kind', 'stages', 'requires', 'parts', 'installs',
             'tools', 'action', 'check', 'fail', 'permission', 'measurements'}
MEASUREMENT = {'label', 'value', 'unit', 'provenance', 'source', 'datum', 'obtained_by'}
PART = {'label', 'kind', 'quantity', 'stages', 'obtained_by'}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'MANUAL_TEXT: {label}')
    return value


def _object(value, label, fields=None):
    if not isinstance(value, dict) or any(not isinstance(k, str) or not k for k in value):
        raise ValueError(f'MANUAL_OBJECT: {label}')
    if fields is not None and set(value) != fields:
        raise ValueError(f'MANUAL_FIELDS: {label}')
    return value


def _strings(value, label, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f'MANUAL_LIST: {label}')
    for item in value:
        _text(item, label)
    if len(set(value)) != len(value):
        raise ValueError(f'MANUAL_DUPLICATE: {label}')
    return value


def _quantity(value, method, label):
    if value is None:
        _text(method, f'{label}.obtained_by')
    elif type(value) is not int or value <= 0:
        raise ValueError(f'MANUAL_QUANTITY: {label}')
    elif method is not None:
        raise ValueError(f'MANUAL_QUANTITY: known {label} cannot have a pending method')


def _numeric(value):
    if isinstance(value, list):
        return bool(value) and all(_numeric(v) for v in value)
    return type(value) in (int, float) and math.isfinite(value)


def validate(document, *, expected_scope, measurement_rules):
    """Validate structure, scope, quantities and a stage-specific dependency graph.

    The caller owns the measurement policy. Unknown values need a method; they
    propagate through dependent operations as missing data, never as approvals.
    No mutation, inferred dimensions, file access or physical execution occurs.
    """
    d = _object(document, 'document', DOCUMENT)
    if d['schema'] != SCHEMA:
        raise ValueError('MANUAL_SCHEMA')
    scope = _object(d['scope'], 'scope', {'design_id', 'revision', 'drive'})
    for key, value in scope.items():
        _text(value, key)
    if scope != expected_scope:
        raise ValueError('MANUAL_SCOPE: wrong design, revision or drive')
    stages = _object(d['stages'], 'stages')
    if not stages:
        raise ValueError('MANUAL_STAGES: empty')
    for title in stages.values():
        _text(title, 'stage title')

    def applicable(values, label):
        values = _strings(values, label, nonempty=True)
        if not set(values) <= set(stages):
            raise ValueError(f'MANUAL_STAGE: {label}')
        return values

    parts = _object(d['parts'], 'parts')
    for ref, part in parts.items():
        _object(part, ref, PART)
        _text(part['label'], ref)
        if part['kind'] not in ('body', 'electrical', 'hardware'):
            raise ValueError(f'MANUAL_PART_KIND: {ref}')
        applicable(part['stages'], ref)
        _quantity(part['quantity'], part['obtained_by'], ref)

    measurements = _object(d['measurements'], 'measurements')
    rules = _object(measurement_rules, 'measurement rules')
    if set(rules) != set(measurements):
        raise ValueError('MANUAL_MEASUREMENT_POLICY: incomplete')
    for key, m in measurements.items():
        _object(m, key, MEASUREMENT)
        rule = _object(rules[key], key, {'unit', 'allowed_provenance', 'require_datum'})
        _text(rule['unit'], key)
        _strings(rule['allowed_provenance'], key, nonempty=True)
        if type(rule['require_datum']) is not bool:
            raise ValueError(f'MANUAL_POLICY: {key}')
        for field in ('label', 'unit', 'provenance', 'source'):
            _text(m[field], f'{key}.{field}')
        if m['unit'] != rule['unit'] or m['provenance'] not in rule['allowed_provenance']:
            raise ValueError(f'MANUAL_MEASUREMENT_POLICY: {key}')
        if m['value'] is None:
            _text(m['obtained_by'], f'{key}.obtained_by')
        else:
            if not _numeric(m['value']) or m['obtained_by'] is not None:
                raise ValueError(f'MANUAL_MEASUREMENT_VALUE: {key}')
            if rule['require_datum']:
                _text(m['datum'], f'{key}.datum')
        if m['datum'] is not None:
            _text(m['datum'], f'{key}.datum')

    operations = d['operations']
    if not isinstance(operations, list) or not operations:
        raise ValueError('MANUAL_OPERATIONS: empty')
    by_id = {}
    missing = {}
    for op in operations:
        _object(op, 'operation', OPERATION)
        for field in ('id', 'title', 'action', 'check', 'fail', 'permission'):
            _text(op[field], field)
        key = op['id']
        if not re.fullmatch(r'[a-z][a-z0-9_-]*', key) or key in by_id:
            raise ValueError(f'MANUAL_OPERATION_ID: {key}')
        by_id[key] = op
        if op['kind'] not in ('preparation', 'assembly', 'electrical'):
            raise ValueError(f'MANUAL_OPERATION_KIND: {key}')
        applicable(op['stages'], key)
        _object(op['requires'], key)
        if set(op['requires']) != set(op['stages']):
            raise ValueError(f'MANUAL_PREREQUISITES: {key}')
        for dependencies in op['requires'].values():
            _strings(dependencies, key)
        _strings(op['tools'], key)
        _strings(op['parts'], key)
        _strings(op['measurements'], key)
        if not set(op['parts']) <= set(parts) or not set(op['measurements']) <= set(measurements):
            raise ValueError(f'MANUAL_REFERENCE: {key}')
        for ref in op['parts']:
            if not set(op['stages']) <= set(parts[ref]['stages']):
                raise ValueError(f'MANUAL_PART_STAGE: {key}/{ref}')
        installs = _object(op['installs'], key)
        if not set(installs) <= set(op['parts']):
            raise ValueError(f'MANUAL_INSTALL_REFERENCE: {key}')
        for ref, count in installs.items():
            _quantity(count, parts[ref]['obtained_by'] if count is None else None, key)
            if count is None and parts[ref]['quantity'] is not None:
                raise ValueError(f'MANUAL_INSTALL_QUANTITY: {key}/{ref}')
        missing[key] = {m for m in op['measurements'] if measurements[m]['value'] is None}
        missing[key].update('part:' + p for p in op['parts'] if parts[p]['quantity'] is None)

    orders, incomplete_by_stage, totals = {}, {}, {}
    for stage in stages:
        active = {key: op for key, op in by_id.items() if stage in op['stages']}
        for key, op in active.items():
            if not set(op['requires'][stage]) <= set(active):
                raise ValueError(f'MANUAL_DEPENDENCY: {stage}/{key}')
        order = []
        pending = set(active)
        while pending:
            ready = [key for key in active if key in pending and
                     set(active[key]['requires'][stage]) <= set(order)]
            if not ready:
                raise ValueError(f'MANUAL_CYCLE: {stage}')
            order.extend(ready)
            pending.difference_update(ready)
        orders[stage] = order
        pending_data = {}
        for key in order:
            inherited = set(missing[key])
            for dependency in active[key]['requires'][stage]:
                inherited.update(pending_data[dependency])
            pending_data[key] = inherited
        incomplete_by_stage[stage] = {key: sorted(values) for key, values in pending_data.items() if values}
        totals[stage] = {}
        for ref, part in parts.items():
            if part['kind'] != 'hardware' or stage not in part['stages']:
                continue
            counts = [op['installs'][ref] for op in active.values() if ref in op['installs']]
            if not counts:
                raise ValueError(f'MANUAL_UNUSED_HARDWARE: {stage}/{ref}')
            total = None if None in counts else sum(counts)
            if part['quantity'] is not None and total != part['quantity']:
                raise ValueError(f'MANUAL_HARDWARE_TOTAL: {stage}/{ref}')
            totals[stage][ref] = total
    incomplete = {}
    for values in incomplete_by_stage.values():
        for key, refs in values.items():
            incomplete.setdefault(key, set()).update(refs)
    return {'schema': SCHEMA, 'identity_sha256': value_digest({'manual': d, 'measurement_rules': rules}),
            'order': orders, 'hardware_totals': totals, 'incomplete_by_stage': incomplete_by_stage,
            'incomplete': {key: sorted(refs) for key, refs in incomplete.items()}, 'physical_approval': False}
