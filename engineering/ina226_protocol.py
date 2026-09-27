"""INA226 measurement record integrity; no device I/O and no physical approval.

Device timing/calibration constants: TI SBOS547C (August 2026), sections
6.4.1, 6.5 and 7.1, https://www.ti.com/lit/ds/symlink/ina226.pdf .
Revision 1 covers continuous shunt-and-bus mode. Physical suitability belongs
to the measurement procedure and authoritative gate validator.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
import math
from pathlib import Path

from engineering import iteration_records as I
from engineering.facts import FactManifest, FactStatus
from engineering.instruction_inputs import value_digest

SCHEMA = 'engineering-ina226-measurement-v1'
CONVERSION_US = (140, 204, 332, 588, 1100, 2116, 4156, 8244)
AVERAGES = (1, 4, 16, 64, 128, 256, 512, 1024)
IDENTITY_FIELDS = ('instrument', 'runtime', 'executor', 'recorded_at', 'conditions')
FILE_FIELDS = ('iteration', 'measurement_firmware', 'measurement_build', 'raw_log',
               'calibration_check', 'peak_check', 'validator_decision')
PARAMETERS = {
    'i2c_address': {'unit': 'address', 'provenance': ['MEASURED']},
    'shunt_resistance_ohm': {'unit': 'ohm', 'provenance': ['MEASURED', 'BENCH_VALIDATED'], 'datum': True},
    'shunt_tolerance_fraction': {'unit': 'ratio', 'provenance': ['MEASURED', 'DATASHEET']},
    'shunt_power_rating_w': {'unit': 'W', 'provenance': ['DATASHEET', 'BENCH_VALIDATED']},
    'current_lsb_a': {'unit': 'A/count', 'provenance': ['CONFIGURED']},
    'calibration_register': {'unit': 'register', 'provenance': ['MEASURED']},
    'config_register': {'unit': 'register', 'provenance': ['MEASURED']},
    'shunt_conversion_us': {'unit': 'us', 'provenance': ['CONFIGURED']},
    'bus_conversion_us': {'unit': 'us', 'provenance': ['CONFIGURED']},
    'averaging': {'unit': 'count', 'provenance': ['CONFIGURED']},
    'logging_hz': {'unit': 'Hz', 'provenance': ['CONFIGURED']},
    'minimum_event_us': {'unit': 'us', 'provenance': ['REQUIREMENT']},
    'required_samples_per_event': {'unit': 'count', 'provenance': ['REQUIREMENT']},
    'peak_error_limit_fraction': {'unit': 'ratio', 'provenance': ['REQUIREMENT']},
    'calibration_error_limit_fraction': {'unit': 'ratio', 'provenance': ['REQUIREMENT']},
    'ambient_temp_c': {'unit': 'degC', 'provenance': ['MEASURED']},
}
SETTINGS = ('i2c_address', 'shunt_resistance_ohm', 'current_lsb_a', 'calibration_register',
            'config_register', 'shunt_conversion_us', 'bus_conversion_us', 'averaging',
            'logging_hz', 'ambient_temp_c', 'shunt_tolerance_fraction', 'shunt_power_rating_w')


def empty_record(session_id):
    I.text(session_id, 'session_id')
    unknown = lambda label: {'value': None, 'obtained_by': 'Записать фактическое значение: ' + label}
    record = {'schema': SCHEMA, 'session_id': session_id, 'instrument_model': 'INA226',
              **{key: unknown(key) for key in IDENTITY_FIELDS}}
    record.update({key: {'path': None, 'hash_basis': 'raw', 'bytes': None, 'sha256': None,
                        'obtained_by': 'Получить и сохранить исходный файл: ' + key} for key in FILE_FIELDS})
    record['parameters'] = {key: {**unknown(key), 'unit': spec['unit'], 'provenance': 'UNKNOWN',
                                 'source': None, 'datum': None, 'scope': session_id}
                            for key, spec in PARAMETERS.items()}
    return record


def settings_digest(record):
    return value_digest({key: {field: record['parameters'][key][field] for field in ('value', 'unit')}
                         for key in SETTINGS} | {'firmware': record['measurement_firmware']['sha256'],
                         'runtime': record['runtime']['value'], 'conditions': record['conditions']['value']})


def _number(value, label, *, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (integer and type(value) is not int):
        raise ValueError(f'{label}: finite {"integer" if integer else "number"} required')
    return value


def _date(value, label):
    if datetime.fromisoformat(I.text(value, label)).tzinfo is None:
        raise ValueError(f'{label}: timezone required')


def _configuration(values):
    for key, number in values.items():
        _number(number, key, integer=key in {'i2c_address', 'config_register', 'calibration_register',
                                            'averaging', 'required_samples_per_event'})
        if key != 'ambient_temp_c' and number <= 0:
            raise ValueError(f'{key}: must be positive')
        if key.endswith('_fraction') and number >= 1:
            raise ValueError(f'{key}: must be below one')
    config, calibration = values['config_register'], values['calibration_register']
    if not 0x40 <= values['i2c_address'] <= 0x4F:
        raise ValueError('I2C_ADDRESS: not an INA226 address')
    if not 0 <= config <= 0x7FFF or config & 7 != 7:
        raise ValueError('CONFIGURATION: revision 1 requires continuous shunt and bus mode, no reset bit')
    actual = (CONVERSION_US[(config >> 3) & 7], CONVERSION_US[(config >> 6) & 7], AVERAGES[(config >> 9) & 7])
    declared = tuple(values[key] for key in ('shunt_conversion_us', 'bus_conversion_us', 'averaging'))
    if actual != declared:
        raise ValueError('CONFIGURATION: readback disagrees with declared timing/averaging')
    expected = int(Decimal('0.00512') / (Decimal(str(values['current_lsb_a'])) *
                                        Decimal(str(values['shunt_resistance_ohm']))))
    if not 1 <= calibration <= 32767 or calibration != expected:
        raise ValueError('CALIBRATION: readback disagrees with shunt and current LSB')
    cycle = (actual[0] + actual[1]) * actual[2]
    if values['logging_hz'] > 1_000_000 / cycle:
        raise ValueError('SAMPLING: requested unique-sample rate exceeds nominal conversion rate')
    if values['required_samples_per_event'] < 2:
        raise ValueError('SAMPLING: explicitly require multiple samples per event')
    return cycle


def _log(path, record, iteration, values):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(rows) < 3 or not isinstance(rows[0], dict):
        raise ValueError('RAW_LOG: header and at least two samples required')
    expected = dict(schema='ina226-raw-v1', session_id=record['session_id'],
        instrument_id=record['instrument']['value'], iteration_id=iteration['iteration_id'],
        measurement_firmware_sha256=record['measurement_firmware']['sha256'],
        config_register=values['config_register'], calibration_register=values['calibration_register'])
    if any(rows[0].get(key) != value for key, value in expected.items()):
        raise ValueError('RAW_LOG_BINDING: another instrument, iteration, firmware or configuration')
    times, currents = [], []
    previous_sequence = None
    for sample in rows[1:]:
        for key in ('sequence', 't_us', 'shunt_raw', 'bus_raw', 'current_raw', 'mask_enable'):
            _number(sample[key], key, integer=True)
        if sample['sequence'] < 0 or (previous_sequence is not None and sample['sequence'] != previous_sequence + 1):
            raise ValueError('RAW_LOG: skipped or duplicate sample sequence')
        previous_sequence = sample['sequence']
        if sample['t_us'] < 0 or (times and sample['t_us'] <= times[-1]):
            raise ValueError('RAW_LOG: timestamps must increase')
        if not 0 <= sample['mask_enable'] <= 65535 or sample['mask_enable'] & 4 or not sample['mask_enable'] & 8:
            raise ValueError('RAW_LOG: overflow or conversion not ready')
        if any(not -32768 <= sample[k] <= 32767 for k in ('shunt_raw', 'current_raw')) or not 0 <= sample['bus_raw'] <= 32767:
            raise ValueError('RAW_LOG: register outside its numeric range')
        times.append(sample['t_us'])
        currents.append(sample['current_raw'])
    gap = max(b-a for a,b in zip(times,times[1:]))
    if values['minimum_event_us'] / gap < values['required_samples_per_event']:
        raise ValueError('PEAK_COVERAGE: measured log gaps cannot meet the declared event resolution')
    return {'sample_count': len(times), 'maximum_logged_gap_us': gap,
            'maximum_absolute_sample_a': max(abs(v) for v in currents) * values['current_lsb_a']}


def _comparison(root, path, kind, record, iteration, values):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('kind') != kind:
        raise ValueError(f'{kind}: comparison schema missing')
    if data.get('instrument_id') != record['instrument']['value'] or data.get('settings_sha256') != settings_digest(record):
        raise ValueError(f'{kind}: evidence belongs to another instrument or settings')
    reference = I.text(data.get('reference_instrument_id'), kind + '.reference_instrument_id')
    if reference == record['instrument']['value']:
        raise ValueError(f'{kind}: an independent reference is required')
    for key in ('reference_calibration', 'raw_log', 'raw_reference'):
        I.pin(root, data[key], kind + '.' + key)
    _log(root / data['raw_log']['path'], record, iteration, values)
    if data['raw_log']['sha256'] == data['raw_reference']['sha256']:
        raise ValueError(f'{kind}: reference data cannot be the same readings')
    I.text(data.get('evaluated_by'), kind + '.evaluated_by')
    _date(data.get('evaluated_at'), kind + '.evaluated_at')
    error = _number(data.get('maximum_relative_error'), kind + '.maximum_relative_error')
    if error < 0 or data.get('verdict') not in ('PASS', 'FAIL'):
        raise ValueError(f'{kind}: an original result and nonnegative error are required')
    limit = values['peak_error_limit_fraction' if kind == 'peak_check' else 'calibration_error_limit_fraction']
    if data['verdict'] == 'PASS' and error > limit:
        raise ValueError(f'{kind}: PASS contradicts the declared error limit')
    if kind == 'peak_check':
        pulse = _number(data.get('minimum_pulse_us'), 'peak_check.minimum_pulse_us')
        I.text(data.get('phases_tested'), 'peak_check.phases_tested')
        if pulse <= 0 or pulse > values['minimum_event_us']:
            raise ValueError('PEAK_SCOPE: proof does not cover the minimum event duration')
    return data['verdict']


def inspect(root, record):
    root = Path(root)
    errors, missing = [], []
    values, identities, paths, statistics, verdicts = {}, {}, {}, {}, {}

    def read(call):
        try:
            return call()
        except I.Missing as exc:
            missing.append(str(exc))
        except (ValueError, TypeError, KeyError, OSError, ArithmeticError) as exc:
            errors.append(str(exc))
        return None

    fields = {'schema', 'session_id', 'instrument_model', 'parameters', *IDENTITY_FIELDS, *FILE_FIELDS}
    if not isinstance(record, dict) or set(record) != fields or record.get('schema') != SCHEMA:
        return I.result(['MEASUREMENT_SCHEMA: missing or unsupported fields'], [])
    read(lambda: I.text(record['session_id'], 'session_id'))
    if record['instrument_model'] != 'INA226':
        errors.append('INSTRUMENT: this procedure covers INA226 only')
    for field in IDENTITY_FIELDS:
        identities[field] = read(lambda: I.value(record[field], field))
    if identities['recorded_at'] is not None:
        read(lambda: _date(identities['recorded_at'], 'recorded_at'))
    for field in FILE_FIELDS:
        paths[field] = read(lambda: I.pin(root, record[field], field))
    if paths['measurement_firmware'] is not None and paths['measurement_build'] is not None:
        read(lambda: I.build_image(paths['measurement_build'], record['measurement_firmware']))
    manifest = read(lambda: FactManifest(record))
    for key, spec in PARAMETERS.items():
        def fact():
            entry = record['parameters'].get(key)
            if not isinstance(entry, dict) or not entry.get('obtained_by'):
                raise ValueError(f'{key}: obtained_by is required')
            if entry.get('value') is None:
                raise I.Missing(key + ': ' + I.text(entry['obtained_by'], key))
            resolved = manifest.resolve(key, expected_unit=spec['unit'], allowed_provenance=spec['provenance'],
                require_datum=spec.get('datum', False), expected_scope=record['session_id'])
            if resolved.status != FactStatus.READY:
                raise ValueError(f'{key}: {resolved.status}')
            I.text(resolved.source, key + '.source')
            return resolved.value
        if manifest is not None:
            values[key] = read(fact)
    if paths['iteration'] is not None:
        iteration = read(lambda: json.loads(paths['iteration'].read_text(encoding='utf-8')))
        if iteration is not None:
            reviewed = I.inspect(root, iteration)
            errors += reviewed['errors']
            missing += reviewed['missing']
    if not errors and not missing:
        cycle = read(lambda: _configuration(values))
        if cycle is not None:
            statistics['nominal_cycle_us'] = cycle
            stats = read(lambda: _log(paths['raw_log'], record, iteration, values))
            if stats is not None:
                statistics.update(stats)
            for kind in ('calibration_check', 'peak_check'):
                verdicts[kind] = read(lambda: _comparison(root, paths[kind], kind, record, iteration, values))
    return dict(I.result(errors, missing), statistics=statistics, procedure_results=verdicts)
