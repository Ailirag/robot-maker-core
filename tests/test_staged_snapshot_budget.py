"""A large registry gets bounded phases, without accepting incomplete checks."""
import os
import pytest
from engineering import staged_snapshot as S

def run(tmp_path, monkeypatch, script, **limits):
    monkeypatch.setattr(S, "CHECK", script)
    return S._run_check(tmp_path, dict(os.environ), **limits)

def test_progress_allows_several_bounded_phases(tmp_path,monkeypatch):
    script='import json,time\nfor i in range(3):\n print(json.dumps({"phase":str(i)}),flush=True)\n time.sleep(.3)\nprint(json.dumps({"result":["STALE: fixture"]}),flush=True)'
    assert run(tmp_path,monkeypatch,script,phase_timeout=.75,total_timeout=3)==["STALE: fixture"]

def test_stalled_phase_refuses(tmp_path,monkeypatch):
    script='import json,time\nprint(json.dumps({"phase":"slow view"}),flush=True)\ntime.sleep(5)'
    with pytest.raises(TimeoutError,match="slow view"):
        run(tmp_path,monkeypatch,script,phase_timeout=.4,total_timeout=2)

def test_progress_does_not_remove_total_budget(tmp_path,monkeypatch):
    script='import json,time\nfor i in range(100):\n print(json.dumps({"phase":str(i)}),flush=True)\n time.sleep(.05)'
    with pytest.raises(TimeoutError,match="total"):
        run(tmp_path,monkeypatch,script,phase_timeout=1,total_timeout=.5)

@pytest.mark.parametrize("script",[
    'print("not json")',
    'print(\'{"result":{}}\')',
    'pass',
    'print(\'{"result":[]}\'); raise SystemExit(1)',
    'print(\'{"result":[]}\'); print(\'{"phase":"after result"}\')',
])
def test_invalid_or_incomplete_check_cannot_pass(tmp_path,monkeypatch,script):
    with pytest.raises(ValueError):
        run(tmp_path,monkeypatch,script,phase_timeout=2,total_timeout=3)

def test_real_driver_preserves_every_view_and_prerequisite(tmp_path,capsys):
    files={
        "engineering/__init__.py":"",
        "engineering/instructions/__init__.py":"",
        "engineering/instructions/check.py":"def problems(): return ['stage finding']\n",
        "engineering/project_contract.py":"def project_module(name):\n from engineering import fixture\n return fixture\n",
        "engineering/fixture.py":(
            "from dataclasses import dataclass\n"
            "@dataclass\nclass View:\n label:str\n fresh:object\n"
            "def views(): return [View('one',lambda:['first']),View('two',lambda:['second'])]\n"
            "def stale(): return [item for view in views() for item in view.fresh()]\n"),
    }
    for name,content in files.items():
        target=tmp_path/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content,encoding="utf8")
    assert S._run_check(tmp_path,dict(os.environ),phase_timeout=3,total_timeout=5)==[
        "first","second","stage finding"]
    output=capsys.readouterr().out
    assert "freshness: one" in output and "freshness: two" in output
