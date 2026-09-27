"""Print tessellation caches exact geometry, placement, tolerance and runtime."""
import copy
from build123d import Box,Pos
from engineering import mesh_cache as M,artifact_cache as C

def test_mesh_cache_reuses_only_identical_geometry(tmp_path,monkeypatch):
    monkeypatch.setattr(M,"CACHE",C.Cache(tmp_path))
    shape=Box(3,4,5)
    a=M.triangles(shape,.03,.08)
    b=M.triangles(shape,.03,.08)
    assert a==b and M.CACHE.stats=={"hit":1,"miss":1}
    M.triangles(Pos(1,0,0)*shape,.03,.08)
    M.triangles(shape,.04,.08)
    assert M.CACHE.stats=={"hit":1,"miss":3}

def test_tessellation_does_not_mutate_geometry(tmp_path,monkeypatch):
    monkeypatch.setattr(M,"CACHE",C.Cache(tmp_path))
    shape=Box(3,4,5)
    before=M.brep_bytes(shape)
    M.triangles(shape,.03,.08)
    assert M.brep_bytes(shape)==before
