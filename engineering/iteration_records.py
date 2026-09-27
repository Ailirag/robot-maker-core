"""Identity of an installed iteration, without deciding physical permission.

A pinned earlier iteration or owner snapshot supplies the explicit history
edge. An installation observation binds actual instance/revisions/image;
a compiled image alone never establishes that it was installed.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from engineering.build_composition import file_pin
from engineering.instruction_inputs import value_digest
from engineering.firmware_build import SCHEMA as BUILD_SCHEMA, RECORD_FIELDS as BUILD_FIELDS

SCHEMA = 'engineering-physical-iteration-v1'
FIELDS = {'schema', 'iteration_id', 'design_id', 'previous', 'instance',
          'geometry_revision', 'electrical_revision', 'firmware_image',
          'firmware_build', 'installation_record'}
PIN_FIELDS = {'path', 'hash_basis', 'bytes', 'sha256', 'obtained_by'}


class Missing(ValueError):
    pass


def text(value, label):
    if not isinstance(value, str) or not value.strip() or value.strip().upper() in {'UNKNOWN', 'TBD', '?', 'N/A'}:
        raise ValueError(f'{label}: explicit text is required; unknown values use null + obtained_by')
    return value


def value(field, label):
    if not isinstance(field, dict) or set(field) != {'value', 'obtained_by'}:
        raise ValueError(f'{label}: expected value and obtained_by')
    text(field['obtained_by'], label + '.obtained_by')
    if field['value'] is None:
        raise Missing(label + ': ' + field['obtained_by'])
    return text(field['value'], label)


def pin(root, field, label):
    if not isinstance(field, dict) or set(field) != PIN_FIELDS:
        raise ValueError(f'{label}: expected a complete file pin or null + obtained_by')
    text(field['obtained_by'], label + '.obtained_by')
    if field['path'] is None:
        if field['bytes'] is not None or field['sha256'] is not None or field['hash_basis'] != 'raw':
            raise ValueError(f'{label}: incomplete pin fields contradict one another')
        raise Missing(label + ': ' + field['obtained_by'])
    if type(field['bytes']) is not int or field['bytes'] <= 0:
        raise ValueError(f'{label}: evidence file must have a positive integer byte count')
    expected = {k: field[k] for k in ('path', 'hash_basis', 'bytes', 'sha256')}
    if file_pin(Path(root), field['path'], field['hash_basis']) != expected:
        raise ValueError(f'{label}: file changed')
    return Path(root) / field['path']


def result(errors, missing):
    return {'state': 'INVALID' if errors else 'INCOMPLETE' if missing else 'REVIEW_REQUIRED',
            'errors': errors, 'missing': missing, 'physical_approval': False}


def build_image(build_path, image):
    """Bind image bytes to the pinned build receipt, without rejudging history.

    This checks identity, not the full producer/environment contract: source
    trees may have moved since installation. Full build review is separate.
    """
    if image['hash_basis'] != 'raw':
        raise ValueError('BUILD_IMAGE: binary identity must use raw bytes')
    record = json.loads(build_path.read_text(encoding='utf-8'))
    if (not isinstance(record, dict) or set(record) != BUILD_FIELDS
            or record['schema'] != BUILD_SCHEMA or type(record['exit_code']) is not int
            or record['exit_code'] != 0 or record['physical_approval'] is not False
            or record['identity_sha256'] != value_digest({k: v for k, v in record.items() if k != 'identity_sha256'})):
        raise ValueError('BUILD_IMAGE: malformed compile-only receipt')
    if not isinstance(record['outputs'], list) or not any(
            isinstance(output, dict) and output.get('hash_basis') == 'raw'
            and output.get('sha256') == image['sha256'] and output.get('bytes') == image['bytes']
            for output in record['outputs']):
        raise ValueError('BUILD_IMAGE: receipt does not contain the identified image')


def inspect(root: Path, record: dict) -> dict:
    errors, missing = [], []

    def read(call):
        try:
            return call()
        except Missing as exc:
            missing.append(str(exc))
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append(str(exc))
        return None

    if not isinstance(record, dict) or set(record) != FIELDS or record.get('schema') != SCHEMA:
        return result(['ITERATION_SCHEMA: missing or unsupported fields'], [])
    for field in ('iteration_id', 'design_id'):
        read(lambda: text(record[field], field))
    identities = {field: read(lambda: value(record[field], field))
                  for field in ('instance', 'geometry_revision', 'electrical_revision')}
    files = {field: read(lambda: pin(root, record[field], field))
             for field in ('firmware_image', 'firmware_build', 'installation_record')}
    if files['firmware_image'] is not None and files['firmware_build'] is not None:
        read(lambda: build_image(files['firmware_build'], record['firmware_image']))

    def previous():
        entry = record['previous']
        if not isinstance(entry, dict) or set(entry) != {'kind', 'record'}:
            raise ValueError('PREVIOUS: an explicit pinned predecessor is required')
        if entry['kind'] not in ('owner_snapshot', 'iteration'):
            raise ValueError('PREVIOUS: unknown predecessor kind')
        path = pin(root, entry['record'], 'previous.record')
        if entry['kind'] == 'iteration':
            predecessor = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(predecessor, dict) or predecessor.get('schema') != SCHEMA:
                raise ValueError('PREVIOUS: not an iteration record')
            old_id = text(predecessor.get('iteration_id'), 'previous.iteration_id')
            if old_id == record['iteration_id']:
                raise ValueError('PREVIOUS: an iteration cannot supersede itself')
    read(previous)

    def installed():
        data = json.loads(files['installation_record'].read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('INSTALLATION_SCHEMA: expected an object')
        expected = {'iteration_id': record['iteration_id'], **identities}
        if files['firmware_image'] is not None:
            expected['firmware_sha256'] = record['firmware_image']['sha256']
        for field, expected_value in expected.items():
            if expected_value is not None and data.get(field) != expected_value:
                raise ValueError(f'INSTALLATION_BINDING: mismatched {field}')
        text(data.get('observed_by'), 'installation.observed_by')
        at = text(data.get('recorded_at'), 'installation.recorded_at')
        if datetime.fromisoformat(at).tzinfo is None:
            raise ValueError('INSTALLATION_DATE: timezone is required')
    if files['installation_record'] is not None:
        read(installed)
    return result(errors, missing)
