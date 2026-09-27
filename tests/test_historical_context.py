"""Historical prose may be preserved; code, dimensions and changed bytes may not."""
import hashlib,json,subprocess
import pytest
from engineering import historical_context as H

def fixture(tmp_path):
    def git(*args):return subprocess.check_output(["git",*args],cwd=tmp_path,text=True).strip()
    git("init","-q");git("config","user.name","test");git("config","user.email","test@example.invalid")
    raw=b"Historical observations\n"
    (tmp_path/"NOTES.md").write_bytes(raw);git("add",".");git("commit","-qm","original")
    head=git("rev-parse","HEAD")
    (tmp_path/"archive").mkdir();(tmp_path/"archive/NOTES.md").write_bytes(raw)
    (tmp_path/"NOTES.md").write_bytes(b"New observations\n")
    pin=dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    return dict(source={"NOTES.md":pin},bindings={"NOTES.md":dict(path="archive/NOTES.md",commit=head,
            reason="Preserved context of the old experiment, not the current physical gate.")})

def test_archive_keeps_original_hash_and_path_without_repinning(tmp_path):
    s=fixture(tmp_path)
    rows=H.verify(tmp_path,s["source"],s["bindings"])
    assert len(rows)==1 and rows[0]["path"]=="archive/NOTES.md"
    assert rows[0]["sha256"]==s["source"]["NOTES.md"]["sha256"]
    assert rows[0]["original"]=="NOTES.md"

@pytest.mark.parametrize("fault",["archive_bytes","source_pin","commit","escape","code","undefined"])
def test_archive_cannot_waive_drift_or_non_document_inputs(tmp_path,fault):
    s=fixture(tmp_path);binding=s["bindings"]["NOTES.md"]
    if fault=="archive_bytes":(tmp_path/"archive/NOTES.md").write_bytes(b"changed")
    elif fault=="source_pin":s["source"]["NOTES.md"]["sha256"]="f"*64
    elif fault=="commit":binding["commit"]="0"*40
    elif fault=="escape":binding["path"]="../outside.md"
    elif fault=="code":
        s["bindings"]["geometry.py"]=s["bindings"].pop("NOTES.md")
        s["source"]["geometry.py"]=s["source"].pop("NOTES.md")
    else:s["source"].clear()
    with pytest.raises(ValueError):H.verify(tmp_path,s["source"],s["bindings"])
