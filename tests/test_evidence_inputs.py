"""Observe actual Python file/fact reads in an isolated producer process."""
import builtins
import io
import json

import pytest

from engineering.facts import FactManifest


def fixture(root):
    path = root / 'facts.json'
    path.write_text(json.dumps({'parameters': {
        'used': {'value': 2, 'unit': 'mm', 'provenance': 'MEASURED', 'scope': 'fixture'},
        'other': {'value': 3, 'unit': 'mm', 'provenance': 'MEASURED'}}}), encoding='utf-8')
    return path


def consume(path):
    return FactManifest.from_path(path).resolve('used', expected_unit='mm',
        allowed_provenance={'MEASURED'}, expected_scope='fixture', require_datum=False)


def test_observed_files_and_only_consumed_facts(tmp_path):
    from engineering.evidence_inputs import Observation
    path = fixture(tmp_path)
    (tmp_path / 'placement.json').write_text('{"x":1}')
    with Observation(tmp_path) as seen:
        consume(path)
        (tmp_path / 'placement.json').read_text()
    assert [p['path'] for p in seen.files] == ['placement.json']
    assert list(seen.facts[0]['contracts']) == ['used']
    data = json.loads(path.read_text())
    data['parameters']['other']['value'] = 8
    path.write_text(json.dumps(data))
    seen.verify_unchanged()
    data['parameters']['used']['value'] = 8
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='CHANGED'):
        seen.verify_unchanged()


def test_direct_read_of_manifest_keeps_whole_file_pin(tmp_path):
    from engineering.evidence_inputs import Observation
    path = fixture(tmp_path)
    with Observation(tmp_path) as seen:
        consume(path)
        path.read_bytes()
    assert [p['path'] for p in seen.files] == ['facts.json']


def test_input_changed_during_procedure_refuses(tmp_path):
    from engineering.evidence_inputs import Observation
    path = tmp_path / 'input.json'
    path.write_text('before')
    with pytest.raises(ValueError, match='CHANGED'):
        with Observation(tmp_path):
            path.read_text()
            path.write_text('after')


def test_wrappers_restore_even_on_producer_exception(tmp_path):
    from engineering.evidence_inputs import Observation
    before = builtins.open, io.open, FactManifest.__dict__['from_path'], FactManifest.resolve
    with pytest.raises(RuntimeError):
        with Observation(tmp_path):
            raise RuntimeError('producer failure')
    assert before == (builtins.open, io.open, FactManifest.__dict__['from_path'], FactManifest.resolve)


def test_preloaded_fact_manifest_refuses_unobservable_origin(tmp_path):
    from engineering.evidence_inputs import Observation
    manifest = FactManifest.from_path(fixture(tmp_path))
    with pytest.raises(ValueError, match='unobserved'):
        with Observation(tmp_path):
            manifest.resolve('used', expected_unit='mm', allowed_provenance={'MEASURED'})


def test_explicit_native_input_is_checked_before_and_after(tmp_path):
    from engineering.evidence_inputs import Observation
    path = tmp_path / 'native.step'
    path.write_text('before')
    with pytest.raises(ValueError, match='CHANGED'):
        with Observation(tmp_path, explicit=[path]):
            path.write_text('after')


def test_nested_observation_refuses_and_restores(tmp_path):
    from engineering.evidence_inputs import Observation
    with Observation(tmp_path):
        with pytest.raises(ValueError, match='isolated'):
            with Observation(tmp_path):
                pass


def test_output_writes_and_runtime_files_are_not_repo_inputs(tmp_path):
    from engineering.evidence_inputs import Observation
    with Observation(tmp_path) as seen:
        (tmp_path / 'output').write_text('created')
        with open(__file__, encoding='utf-8') as stream:
            stream.read()
    assert seen.files == []


def test_later_fact_read_cannot_repin_a_previously_consumed_fact(tmp_path):
    from engineering.evidence_inputs import Observation
    path = fixture(tmp_path)
    data = json.loads(path.read_text())
    with pytest.raises(ValueError, match='CHANGED'):
        with Observation(tmp_path):
            manifest = FactManifest.from_path(path)
            manifest.resolve('used', expected_unit='mm', allowed_provenance={'MEASURED'})
            data['parameters']['used']['value'] = 99
            path.write_text(json.dumps(data))
            manifest.resolve('other', expected_unit='mm', allowed_provenance={'MEASURED'})
