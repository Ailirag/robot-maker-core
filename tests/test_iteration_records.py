"""Installed identity is distinct from a proposed design or a task status."""
import json

import pytest

from engineering.build_composition import file_pin


def known(value):
    return {'value': value, 'obtained_by': 'Fixture observation, not robot evidence.'}


@pytest.fixture
def iteration(tmp_path):
    for name, payload in [('previous.md', b'fixture owner snapshot'),
                          ('firmware.bin', b'fixture firmware')]:
        (tmp_path / name).write_bytes(payload)
    from engineering.firmware_build import SCHEMA, RECORD_FIELDS
    from engineering.instruction_inputs import value_digest
    build = dict.fromkeys(RECORD_FIELDS)
    build.update(schema=SCHEMA, exit_code=0, physical_approval=False,
                 outputs=[file_pin(tmp_path, 'firmware.bin')])
    build['identity_sha256'] = value_digest({k: v for k, v in build.items() if k != 'identity_sha256'})
    (tmp_path / 'build.json').write_text(json.dumps(build), encoding='utf-8')
    pin = lambda name: dict(file_pin(tmp_path, name), obtained_by='Fixture file.')
    record = dict(schema='engineering-physical-iteration-v1', iteration_id='iteration-fixture-2',
        design_id='fixture-design', previous={'kind': 'owner_snapshot', 'record': pin('previous.md')},
        instance=known('fixture-body-1'), geometry_revision=known('geometry-2'),
        electrical_revision=known('electrical-3'), firmware_image=pin('firmware.bin'),
        firmware_build=pin('build.json'))
    installation = {'iteration_id': record['iteration_id'], 'instance': 'fixture-body-1',
        'geometry_revision': 'geometry-2', 'electrical_revision': 'electrical-3',
        'firmware_sha256': record['firmware_image']['sha256'],
        'observed_by': 'fixture observer', 'recorded_at': '2026-09-07T00:00:00+00:00'}
    (tmp_path / 'installation.json').write_text(json.dumps(installation), encoding='utf-8')
    record['installation_record'] = pin('installation.json')
    return tmp_path, record


def test_complete_identity_is_only_ready_for_review(iteration):
    from engineering.iteration_records import inspect
    result = inspect(*iteration)
    assert result['state'] == 'REVIEW_REQUIRED'
    assert result['physical_approval'] is False


@pytest.mark.parametrize('field', ['instance', 'geometry_revision', 'electrical_revision'])
def test_unknown_identity_requires_obtained_by_and_is_incomplete(iteration, field):
    from engineering.iteration_records import inspect
    iteration[1][field] = {'value': None, 'obtained_by': 'Read the actual installed label.'}
    assert inspect(*iteration)['state'] == 'INCOMPLETE'
    iteration[1][field].pop('obtained_by')
    assert inspect(*iteration)['state'] == 'INVALID'


def test_installed_evidence_from_another_instance_refuses(iteration):
    from engineering.iteration_records import inspect
    iteration[1]['instance'] = known('another-body')
    assert inspect(*iteration)['state'] == 'INVALID'


def test_changed_firmware_and_missing_previous_refuse(iteration):
    from engineering.iteration_records import inspect
    (iteration[0] / 'firmware.bin').write_bytes(b'changed')
    assert inspect(*iteration)['state'] == 'INVALID'
    iteration[1].pop('previous')
    assert inspect(*iteration)['state'] == 'INVALID'


def test_iteration_transition_names_the_previous_iteration(iteration):
    from engineering.iteration_records import inspect
    root, record = iteration
    (root / 'previous.json').write_text(json.dumps({'schema': record['schema'], 'iteration_id': 'iteration-fixture-1'}))
    record['previous'] = {'kind': 'iteration', 'record': dict(file_pin(root, 'previous.json'), obtained_by='Fixture')}
    assert inspect(root, record)['state'] == 'REVIEW_REQUIRED'
    record['iteration_id'] = 'iteration-fixture-1'
    assert inspect(root, record)['state'] == 'INVALID'


def test_missing_installation_is_not_implied_by_a_binary(iteration):
    from engineering.iteration_records import inspect
    iteration[1]['installation_record'] = {'path': None, 'sha256': None, 'bytes': None,
        'hash_basis': 'raw', 'obtained_by': 'Record the installed instance and image after authorized work.'}
    assert inspect(*iteration)['state'] == 'INCOMPLETE'


def test_malformed_installation_payload_returns_a_refusal(iteration):
    from engineering.iteration_records import inspect
    root, record = iteration
    path = root / record['installation_record']['path']
    path.write_text('[]')
    record['installation_record'] = dict(file_pin(root, path.name), obtained_by='Fixture')
    assert inspect(root, record)['state'] == 'INVALID'


def test_foreign_build_cannot_describe_the_installed_image(iteration):
    from engineering.iteration_records import inspect
    from engineering.instruction_inputs import value_digest
    root, record = iteration
    path = root / 'build.json'
    build = json.loads(path.read_text())
    build['outputs'][0]['sha256'] = '0' * 64
    build['identity_sha256'] = value_digest({k: v for k, v in build.items() if k != 'identity_sha256'})
    path.write_text(json.dumps(build))
    record['firmware_build'] = dict(file_pin(root, path.name), obtained_by='Foreign fixture build')
    assert inspect(root, record)['state'] == 'INVALID'


def test_binary_identity_cannot_normalize_line_endings(iteration):
    from engineering.iteration_records import inspect
    root, record = iteration
    record['firmware_image'] = dict(file_pin(root, 'firmware.bin', 'canonical_lf'), obtained_by='Fixture')
    assert inspect(root, record)['state'] == 'INVALID'
