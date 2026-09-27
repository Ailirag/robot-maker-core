import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_success_exit_without_freecad_report_is_not_a_runtime(monkeypatch):
    from engineering import runtime_probe as R
    monkeypatch.setattr(R.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr=''))
    with pytest.raises(ValueError, match='FREECAD'):
        R.freecad('fixture-program')


def test_freecad_report_is_observed_in_a_new_process(monkeypatch):
    from engineering import runtime_probe as R
    def run(*args, **kwargs):
        assert 'PYTHONPATH' not in kwargs['env']
        Path(kwargs['env']['ENGINEERING_RUNTIME_REPORT']).write_text(json.dumps({
            'version': '1.1.1', 'python': '3.11.14', 'occ': '7.8.1'}))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(R.subprocess, 'run', run)
    assert R.freecad('fixture-program')['occ'] == '7.8.1'


def test_sdk_manifest_collection_refuses_incomplete_package(tmp_path):
    from engineering import runtime_probe as R
    package = tmp_path / 'packages/compiler'
    package.mkdir(parents=True)
    with pytest.raises(ValueError, match='SDK'):
        R.sdk(tmp_path)
    (package / 'package.json').write_text(json.dumps({'name':'compiler','version':'1.0'}))
    assert R.sdk(tmp_path) == {'compiler':'1.0'}
    (package / 'package.json').write_text(json.dumps({'name':'other','version':'1.0'}))
    with pytest.raises(ValueError, match='SDK'):
        R.sdk(tmp_path)
