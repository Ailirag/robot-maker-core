"""The public doctor is read-only and never calls an installer."""
from pathlib import Path
import json


def test_doctor_reports_a_missing_runtime_without_bootstrapping(tmp_path, monkeypatch, capsys):
    from engineering import environment as E
    policy = tmp_path / 'policy.json'; policy.write_text('{}')
    def no_bootstrap(*args, **kwargs):
        raise AssertionError('doctor must not install')
    monkeypatch.setattr(E.T, 'bootstrap', no_bootstrap)
    monkeypatch.setattr(E.R, 'observe', lambda *args: (_ for _ in ()).throw(ValueError('missing fixture runtime')))
    assert E.main(['--root',str(tmp_path),'--policy','policy.json','doctor',
                   '--sdk-root',str(tmp_path/'missing'),'--freecadcmd','missing']) == 4
    report = json.loads(capsys.readouterr().out)
    assert report['result']['status'] == 'BLOCKED'
    assert report['physical_approval'] is False


def test_sdk_installer_refuses_a_path_outside_the_supported_budget(tmp_path):
    import pytest
    from engineering import environment as E
    directory = tmp_path / ('long'*20)
    with pytest.raises(ValueError, match='SDK_PATH'):
        E.install_sdk(tmp_path, {'max_sdk_root_chars':8}, directory,
                      executable='unused', project_dir='unused', environment='unused')
    assert not directory.exists()
