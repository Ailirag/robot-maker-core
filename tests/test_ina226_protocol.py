"""Synthetic protocol records, never measurements of the robot."""
from copy import deepcopy
import json

import pytest

from engineering.build_composition import file_pin
from test_iteration_records import iteration, known


@pytest.fixture
def measurement(iteration):
    from engineering import ina226_protocol as P
    root, installed = iteration
    (root / 'iteration.json').write_text(json.dumps(installed), encoding='utf-8')
    record = P.empty_record('session-fixture')
    for field in P.IDENTITY_FIELDS:
        record[field] = known({'instrument': 'fixture-meter', 'recorded_at': '2026-09-07T00:00:00+00:00'}.get(field, 'fixture'))
    pin = lambda name: dict(file_pin(root, name), obtained_by='Fixture file.')
    for field in P.FILE_FIELDS:
        if not (root / (field + '.json')).exists():
            (root / (field + '.json')).write_text('{}')
        record[field] = pin(field + '.json')
    record['iteration'] = pin('iteration.json')
    record['measurement_firmware'] = pin('firmware.bin')
    record['measurement_build'] = pin('build.json')
    values = {'i2c_address': 0x40, 'shunt_resistance_ohm': .01, 'shunt_tolerance_fraction': .01,
        'shunt_power_rating_w': 1, 'current_lsb_a': .001, 'calibration_register': 512,
        'config_register': 0x4007, 'shunt_conversion_us': 140, 'bus_conversion_us': 140,
        'averaging': 1, 'logging_hz': 1000, 'minimum_event_us': 10000,
        'required_samples_per_event': 5, 'peak_error_limit_fraction': .1,
        'calibration_error_limit_fraction': .02, 'ambient_temp_c': 20}
    for key, number in values.items():
        item = record['parameters'][key]
        item.update(value=number, provenance=P.PARAMETERS[key]['provenance'][0],
                    source='Synthetic fixture', datum='Fixture datum')
    header = dict(schema='ina226-raw-v1', session_id=record['session_id'],
        instrument_id=record['instrument']['value'], iteration_id=installed['iteration_id'],
        measurement_firmware_sha256=record['measurement_firmware']['sha256'],
        config_register=0x4007, calibration_register=512)
    samples = [dict(sequence=i, t_us=i*1000, shunt_raw=100, bus_raw=4000,
                    current_raw=100, mask_enable=8) for i in range(3)]
    (root / 'raw.jsonl').write_text('\n'.join(json.dumps(x) for x in [header,*samples])+'\n', encoding='utf-8')
    record['raw_log'] = pin('raw.jsonl')
    (root / 'reference.log').write_text('Independent synthetic reference samples')
    for name in ('calibration_check', 'peak_check'):
        data = dict(kind=name, instrument_id='fixture-meter', settings_sha256=P.settings_digest(record),
            reference_instrument_id='fixture-reference', reference_calibration=pin('previous.md'),
            raw_log=pin('raw.jsonl'), raw_reference=pin('reference.log'),
            maximum_relative_error=.01, verdict='PASS',
            evaluated_by='fixture observer', evaluated_at='2026-09-07T00:00:00+00:00')
        if name == 'peak_check':
            data.update(minimum_pulse_us=10000, phases_tested='Fixture pulses shifted across conversion windows')
        (root / (name+'.json')).write_text(json.dumps(data), encoding='utf-8')
        record[name] = pin(name+'.json')
    return root, record


def test_empty_record_is_explicitly_incomplete():
    from engineering import ina226_protocol as P
    record = P.empty_record('fixture-draft')
    result = P.inspect('.', record)
    assert result['state'] == 'INCOMPLETE' and not result['errors']
    assert result['physical_approval'] is False
    assert all(f['value'] is None and f['obtained_by'] for f in record['parameters'].values())


def test_complete_fixture_only_requests_authority_review(measurement):
    from engineering import ina226_protocol as P
    result = P.inspect(*measurement)
    assert result['state'] == 'REVIEW_REQUIRED', result
    assert result['physical_approval'] is False
    assert result['statistics']['nominal_cycle_us'] == 280
    assert result['statistics']['maximum_logged_gap_us'] == 1000


@pytest.mark.parametrize('field', ['instrument', 'runtime', 'executor', 'conditions', 'recorded_at'])
def test_missing_execution_metadata_refuses(measurement, field):
    from engineering import ina226_protocol as P
    measurement[1][field]['value'] = None
    assert P.inspect(*measurement)['state'] == 'INCOMPLETE'


@pytest.mark.parametrize('field', ['shunt_resistance_ohm', 'current_lsb_a', 'averaging'])
def test_measurement_facts_cannot_cross_units_or_scope(measurement, field):
    from engineering import ina226_protocol as P
    measurement[1]['parameters'][field]['scope'] = 'another-session'
    assert P.inspect(*measurement)['state'] == 'INVALID'


@pytest.mark.parametrize('field,value', [('calibration_register',0), ('calibration_register',513),
    ('config_register',0x4127), ('logging_hz',100000), ('shunt_resistance_ohm',True)])
def test_incompatible_registers_or_sampling_refuse(measurement, field, value):
    from engineering import ina226_protocol as P
    measurement[1]['parameters'][field]['value'] = value
    assert P.inspect(*measurement)['state'] == 'INVALID'


@pytest.mark.parametrize('change', ['wrong_instrument','wrong_firmware','overflow','stale','drop','timestamp'])
def test_raw_log_must_match_the_actual_session_and_have_valid_samples(measurement, change):
    from engineering import ina226_protocol as P
    root, record = measurement
    path = root / record['raw_log']['path']
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    if change == 'wrong_instrument': rows[0]['instrument_id'] = 'old-failed-meter'
    if change == 'wrong_firmware': rows[0]['measurement_firmware_sha256'] = '0'*64
    if change == 'overflow': rows[1]['mask_enable'] = 12
    if change == 'stale': rows[1]['mask_enable'] = 0
    if change == 'drop': rows[2]['sequence'] = 99
    if change == 'timestamp': rows[2]['t_us'] = 0
    path.write_text('\n'.join(json.dumps(x) for x in rows), encoding='utf-8')
    record['raw_log'] = dict(file_pin(root, record['raw_log']['path']), obtained_by='Fixture changed log')
    assert P.inspect(root, record)['state'] == 'INVALID'


def test_peak_proof_for_slower_events_cannot_support_fast_events(measurement):
    from engineering import ina226_protocol as P
    root, record = measurement
    path = root / record['peak_check']['path']
    data = json.loads(path.read_text())
    data['minimum_pulse_us'] = 20000
    path.write_text(json.dumps(data))
    record['peak_check'] = dict(file_pin(root, path.name), obtained_by='Fixture')
    assert P.inspect(root, record)['state'] == 'INVALID'


@pytest.mark.parametrize('field', ['logging_hz', 'ambient_temp_c'])
def test_changed_acquisition_conditions_invalidate_comparison(measurement, field):
    from engineering import ina226_protocol as P
    measurement[1]['parameters'][field]['value'] /= 2
    assert P.inspect(*measurement)['state'] == 'INVALID'


def test_failure_is_preserved_as_an_original_result(measurement):
    from engineering import ina226_protocol as P
    root, record = measurement
    path = root / record['calibration_check']['path']
    data = json.loads(path.read_text())
    data.update(verdict='FAIL', maximum_relative_error=.2)
    path.write_text(json.dumps(data))
    record['calibration_check'] = dict(file_pin(root, path.name), obtained_by='Fixture')
    result = P.inspect(root, record)
    assert result['state'] == 'REVIEW_REQUIRED', result
    assert result['procedure_results']['calibration_check'] == 'FAIL'
    assert result['physical_approval'] is False


def test_measurement_build_must_contain_the_used_binary(measurement):
    from engineering import ina226_protocol as P
    root, record = measurement
    (root / 'foreign.bin').write_bytes(b'not this acquisition firmware')
    record['measurement_firmware'] = dict(file_pin(root, 'foreign.bin'), obtained_by='Fixture')
    result = P.inspect(root, record)
    assert any('BUILD_IMAGE' in error for error in result['errors']), result


def test_comparison_log_cannot_belong_to_a_different_instrument(measurement):
    from engineering import ina226_protocol as P
    root, record = measurement
    rows = [json.loads(line) for line in (root / 'raw.jsonl').read_text().splitlines()]
    rows[0]['instrument_id'] = 'other-instrument'
    (root / 'foreign.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
    path = root / record['peak_check']['path']
    data = json.loads(path.read_text())
    data['raw_log'] = dict(file_pin(root, 'foreign.jsonl'), obtained_by='Fixture')
    path.write_text(json.dumps(data))
    record['peak_check'] = dict(file_pin(root, path.name), obtained_by='Fixture')
    assert P.inspect(root, record)['state'] == 'INVALID'
