"""Disposable render caches must validate keys and bytes before reuse."""
import json
from engineering import artifact_cache as C

def test_cache_hit_change_and_corruption(tmp_path):
    cache=C.Cache(tmp_path);calls=[]
    def build():calls.append(1);return b"result"
    assert cache.get("mesh",{"shape":"a"},build)==b"result"
    assert cache.get("mesh",{"shape":"a"},build)==b"result"
    assert len(calls)==1
    cache.get("mesh",{"shape":"b"},build)
    assert len(calls)==2
    for path in tmp_path.glob("*.json"):path.write_text("{}")
    cache.get("mesh",{"shape":"a"},build)
    assert len(calls)==3
    assert cache.stats["hit"]==1

def test_dry_probe_never_builds_and_detects_missing(tmp_path):
    cache=C.Cache(tmp_path)
    assert cache.probe("x",{})["status"]=="MISS"
    cache.get("x",{},lambda:b"x")
    assert cache.probe("x",{})["status"]=="HIT"
    assert cache.probe("x",{"revision":2})["status"]=="MISS"

def test_forced_mode_bypasses_cache_without_overwriting_it(monkeypatch,tmp_path):
    cache=C.Cache(tmp_path)
    assert cache.get("x",{},lambda:b"old")==b"old"
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setenv("ENGINEERING_CACHE_DISABLE","1")
    assert cache.probe("x",{})["status"]=="BYPASS"
    assert cache.get("x",{},lambda:b"new")==b"new"
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before
    monkeypatch.delenv("ENGINEERING_CACHE_DISABLE")
    assert cache.get("x",{},lambda:b"wrong")==b"old"

def test_failures_are_not_cached(tmp_path):
    cache=C.Cache(tmp_path)
    def fail():raise RuntimeError("failed")
    import pytest
    with pytest.raises(RuntimeError):cache.get("x",{},fail)
    assert cache.probe("x",{})["status"]=="MISS"
