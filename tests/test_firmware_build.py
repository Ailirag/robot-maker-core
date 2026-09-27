"""Producer receipts bind compiled artifacts to declared sources and configuration."""
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def fixture_project(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    (root / 'code').mkdir()
    (root / 'code/platformio.ini').write_text('[env:bench]\n', encoding='utf-8')
    (root / 'code/main.cpp').write_text('source\n', encoding='utf-8')
    (root / 'pins.h').write_text('pins\n', encoding='utf-8')
    contract = dict(build_id='fixture-bench', project_dir='code', environment='bench',
                    sources=['code/platformio.ini', 'code/main.cpp', 'pins.h'],
                    outputs=['firmware.bin'], excluded_local_inputs=['secrets.h'])
    return root, contract


def fake_compiler(monkeypatch, root, *, fail=False, mutate=False):
    from engineering import firmware_build as core
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == '--version':
            return subprocess.CompletedProcess(argv, 0, stdout='PlatformIO Core, version fixture\n', stderr='')
        if mutate:
            (root / 'pins.h').write_text('changed during compile', encoding='utf-8')
        output = root / 'code/.pio/build/bench/firmware.bin'
        output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b'compiled fixture')
        return subprocess.CompletedProcess(argv, 1 if fail else 0, stdout='compiler log\n', stderr='')
    monkeypatch.setattr(core.subprocess, 'run', run)
    return calls


def test_receipt_matches_sources_artifact_and_environment(fixture_project, tmp_path, monkeypatch):
    from engineering import firmware_build as core
    root, contract = fixture_project
    calls = fake_compiler(monkeypatch, root)
    destination = root / 'build'
    record = core.build(root, contract, executable='pio', destination=destination)
    assert core.verify_record(root, contract, record) == []
    assert record['physical_approval'] is False
    assert record['contract'] == contract
    assert calls[-1] == ['pio', 'run', '--project-dir', str(root / 'code'), '-e', 'bench']
    assert not any('upload' in arg for argv in calls for arg in argv)
    assert record['tool_version'] == 'PlatformIO Core, version fixture'
    (root / 'pins.h').write_text('new pins\n', encoding='utf-8')
    assert any('INPUT_DRIFT' in e for e in core.verify_record(root, contract, record))


@pytest.mark.parametrize('mode', ['failure', 'drift', 'local_secret'])
def test_failed_or_changed_build_has_no_success_receipt(fixture_project, monkeypatch, mode):
    from engineering import firmware_build as core
    root, contract = fixture_project
    calls = fake_compiler(monkeypatch, root, fail=mode == 'failure', mutate=mode == 'drift')
    destination = root / 'build'
    if mode == 'local_secret': (root / 'secrets.h').write_text('fixture only', encoding='utf-8')
    with pytest.raises(ValueError):
        core.build(root, contract, executable='pio', destination=destination)
    assert not (destination / 'BUILD.json').exists()
    if mode == 'local_secret': assert calls == []


def test_wrong_stage_or_missing_artifact_refuses_record(fixture_project, monkeypatch):
    from engineering import firmware_build as core
    root, contract = fixture_project
    fake_compiler(monkeypatch, root)
    record = core.build(root, contract, executable='pio', destination=root / 'build')
    another = deepcopy(contract); another['environment'] = 'another'
    assert core.verify_record(root, another, record)
    (root / 'build/firmware.bin').unlink()
    assert core.verify_record(root, contract, record)


def test_existing_destination_is_not_overwritten(fixture_project, monkeypatch):
    from engineering import firmware_build as core
    root, contract = fixture_project
    calls = fake_compiler(monkeypatch, root)
    destination = root / 'build'; destination.mkdir()
    (destination / 'keep').write_bytes(b'keep')
    with pytest.raises(ValueError, match='EXISTS'):
        core.build(root, contract, executable='pio', destination=destination)
    assert calls == []
    assert (destination / 'keep').read_bytes() == b'keep'


def test_producer_code_is_archived_separately_from_compiler_inputs(fixture_project, monkeypatch):
    from engineering import firmware_build as core
    root, contract = fixture_project
    producer = root / 'producer.py'; producer.write_bytes(b'# procedure v1\n')
    monkeypatch.setattr(core, '_producer_sources', lambda: [producer])
    fake_compiler(monkeypatch, root)
    record = core.build(root, contract, executable='pio', destination=root / 'build')
    assert record['producer']['sources']
    assert all('/producer/' in pin['path'] for pin in record['producer']['sources'])
    producer.write_bytes(b'# procedure v2\n')
    assert core.verify_record(root, contract, record) == []
    archive = root / record['producer']['sources'][0]['path']
    archive.write_bytes(b'tampered historical procedure')
    assert core.verify_record(root, contract, record)


@pytest.mark.parametrize('name', ['PLATFORMIO_BUILD_FLAGS', 'PLATFORMIO_SRC_DIR', 'CPLUS_INCLUDE_PATH'])
def test_hidden_build_overrides_fail_before_invoking_tools(fixture_project, monkeypatch, name):
    from engineering import firmware_build as core
    root, contract = fixture_project
    calls = fake_compiler(monkeypatch, root)
    monkeypatch.setenv(name, 'unrecorded-fixture-value')
    with pytest.raises(ValueError, match='BUILD_ENVIRONMENT'):
        core.build(root, contract, executable='pio', destination=root / 'build')
    assert calls == []
    assert not (root / 'build').exists()


def test_compiler_epoch_is_an_explicit_contract_input(fixture_project, monkeypatch):
    from engineering import firmware_build as core
    root, contract = fixture_project
    contract['source_date_epoch'] = 1234567890
    fake_compiler(monkeypatch, root)
    stub = core.subprocess.run
    environments = []
    def run(*args, **kwargs):
        environments.append(kwargs['env'])
        return stub(*args, **kwargs)
    monkeypatch.setattr(core.subprocess, 'run', run)
    monkeypatch.setenv('SOURCE_DATE_EPOCH', '1')
    record = core.build(root, contract, executable='pio', destination=root / 'build')
    assert environments[-1]['SOURCE_DATE_EPOCH'] == '1234567890'
    assert core.verify_record(root, contract, record) == []


def test_runtime_policy_is_checked_and_bound_to_the_receipt(fixture_project, tmp_path, monkeypatch):
    import json
    from engineering import firmware_build as core, runtime_probe as R, toolchain as T
    from engineering.build_composition import file_pin
    root, contract = fixture_project
    (root / 'pins.h').write_bytes(b'pins\n')
    for name in ('requirements.lock', 'contrib.lock'):
        requirement = 'platformio==fixture' if name == 'requirements.lock' else 'fixture==1'
        (root / name).write_text(requirement+' --hash=sha256:'+'a'*64+'\n', encoding='utf-8', newline='\n')
    policy = {'schema': T.SCHEMA, 'id':'fixture-runtime', 'python':'3.14.3',
        'platform':'win32', 'machine':'AMD64', 'requirements':file_pin(root,'requirements.lock'),
        'contrib_requirements':file_pin(root,'contrib.lock'),
        'freecad':{'version':'1.1.1','python':'3.11.14','occ':'7.8.1'},
        'sdk_packages':{'compiler':'1'}, 'source_lf':['pins.h']}
    (root / 'runtime.json').write_text(json.dumps(policy), encoding='utf-8')
    contract['runtime_policy'] = 'runtime.json'
    contract['sources'].append('runtime.json')
    observed = {'python':'3.14.3', 'platform':'win32', 'machine':'AMD64',
        'isolated':True, 'pythonpath':None, 'packages':{'platformio':'fixture'},
        'contrib_packages':{'fixture':'1'}, 'freecad':dict(policy['freecad']), 'sdk_packages':{'compiler':'1'}}
    monkeypatch.setenv('PLATFORMIO_CORE_DIR',str(root/'sdk'))
    monkeypatch.setenv('FREECADCMD','fixture-freecad')
    monkeypatch.setattr(R,'observe',lambda *args:deepcopy(observed))
    calls = fake_compiler(monkeypatch,root)
    bad = deepcopy(observed); bad['sdk_packages'] = {'compiler':'2'}
    monkeypatch.setattr(R,'observe',lambda *args:deepcopy(bad))
    with pytest.raises(ValueError,match='BUILD_RUNTIME'):
        core.build(root,contract,executable='pio',destination=root/'bad')
    assert calls == [] and not (root/'bad').exists()
    monkeypatch.setattr(R,'observe',lambda *args:deepcopy(observed))
    record = core.build(root,contract,executable='pio',destination=root/'good')
    assert record['producer']['runtime'] == observed
    assert core.verify_record(root,contract,record) == []
    from engineering.instruction_inputs import value_digest
    wrong_tool = deepcopy(record)
    wrong_tool['tool_version'] = 'PlatformIO Core, version another'
    wrong_tool['identity_sha256'] = value_digest({k:v for k,v in wrong_tool.items() if k != 'identity_sha256'})
    assert core.verify_record(root,contract,wrong_tool), 'Executable version must agree with recorded runtime'
    stub = core.subprocess.run
    def other_tool(argv, **kwargs):
        if argv[-1] == '--version':
            return subprocess.CompletedProcess(argv, 0, stdout='PlatformIO Core, version another\n', stderr='')
        raise AssertionError('Wrong executable must not compile')
    monkeypatch.setattr(core.subprocess, 'run', other_tool)
    with pytest.raises(ValueError, match='BUILD_TOOL'):
        core.build(root,contract,executable='another-pio',destination=root/'wrong-tool')
    monkeypatch.setattr(core.subprocess, 'run', stub)
    record['producer']['runtime']['sdk_packages']['compiler'] = '2'
    assert core.verify_record(root,contract,record)
    from engineering.instruction_inputs import value_digest
    record['producer']['runtime'] = None
    record['identity_sha256'] = value_digest({k:v for k,v in record.items() if k != 'identity_sha256'})
    assert core.verify_record(root,contract,record), 'Missing historical runtime must not be replaced by a fresh probe'
