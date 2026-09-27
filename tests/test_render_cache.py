"""Rendering work is cached independently from instruction order."""
from pathlib import Path
from engineering import render3d as R,artifact_cache as C

def test_mesh_key_includes_shape_and_tolerance(monkeypatch,tmp_path):
    monkeypatch.setattr(R,"_CACHE",C.Cache(tmp_path))
    monkeypatch.setattr(R,"_mesh_runtime",lambda:{"runtime":"test"})
    class V:
        x=0.;y=0.;z=0.
    class Shape:
        raw="shape-a";calls=0
        def exportBrepToString(self):return self.raw
        def tessellate(self,tol):
            self.calls+=1
            return [V(),V(),V()],[(0,1,2)]
    shape=Shape()
    assert R.triangulate(shape,.6)==R.triangulate(shape,.6)
    assert shape.calls==1
    R.triangulate(shape,.7)
    shape.raw="shape-b";R.triangulate(shape,.6)
    assert shape.calls==3

def test_picture_key_tracks_content_not_output_filename(monkeypatch,tmp_path):
    monkeypatch.setattr(R,"_CACHE",C.Cache(tmp_path/"cache"))
    monkeypatch.setattr(R,"_picture_runtime",lambda:{"runtime":"test"})
    calls=[]
    def paint(path,bodies,**options):
        calls.append(options);path.write_bytes(options["title"].encode());return path
    monkeypatch.setattr(R,"_render_uncached",paint)
    bodies=[R.Body("a", [[(0,0,0),(1,0,0),(0,1,0)]])]
    R.render(tmp_path/"one.png",bodies,title="first")
    R.render(tmp_path/"two.png",bodies,title="first")
    assert len(calls)==1
    R.render(tmp_path/"two.png",bodies,title="second")
    assert len(calls)==2
    assert (tmp_path/"two.png").read_bytes()==b"second"
