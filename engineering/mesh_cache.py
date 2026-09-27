"""Exact build123d print tessellation cache; no geometry or fit approval."""
import copy,io,json,sys
from functools import lru_cache
from pathlib import Path
from importlib.metadata import version
from build123d import export_brep
from OCP.BRepTools import BRepTools
from engineering import artifact_cache as C

CACHE=C.default_cache()

def brep_bytes(shape):
    detached=copy.deepcopy(shape)
    BRepTools.Clean_s(detached.wrapped)
    stream=io.BytesIO()
    if not export_brep(detached,stream):raise ValueError("MESH_CACHE: BREP export failed")
    return stream.getvalue()

@lru_cache(maxsize=1)
def runtime():
    import OCP
    binaries=sorted(Path(OCP.__file__).parent.rglob("*.pyd"))
    binaries+=sorted(Path(OCP.__file__).parent.rglob("*.so"))
    return dict(python=sys.version,build123d=version("build123d"),ocp=getattr(OCP,"__version__",None),
        binaries={str(p):C.digest(p.read_bytes()) for p in binaries})

def triangles(shape,linear=.03,angular=.08):
    inputs=dict(brep=C.digest(brep_bytes(shape)),linear=linear,angular=angular,
        runtime=runtime(),producer=C.digest(Path(__file__).read_bytes()))
    def build():
        detached=copy.deepcopy(shape)
        vertices,faces=detached.tessellate(linear,angular)
        return C.encode(dict(vertices=[[float(c) for c in v] for v in vertices],
                             triangles=[list(t) for t in faces]))
    return json.loads(CACHE.get("print-triangles",inputs,build))
