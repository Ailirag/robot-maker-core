"""Deterministic engineering renders of FreeCAD bodies with explicit colours.

The per-design legacy renderers colour bodies by name; step pictures and sweep pictures
need the opposite: the caller says which body is new, which is blocking and
which is context. Same technique as the committed all-sides renders (BRep
tessellation into Matplotlib orthographic Poly3DCollection), so the pictures
read alike. FreeCAD and Matplotlib are required; nothing here proves fit.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
import json
import sys
import tempfile
from engineering import artifact_cache as AC

_CACHE = AC.default_cache()

def _producer():
    return AC.digest(Path(__file__).read_bytes())

@lru_cache(maxsize=1)
def _mesh_runtime():
    import FreeCAD,Part
    return {"python":sys.version,"freecad":FreeCAD.Version(),
            "occ":getattr(Part,"OCC_VERSION","unknown"),
            "modules":{str(p):AC.digest(p.read_bytes()) for p in
                {Path(sys.executable),Path(Part.__file__),
                 *Path(sys.executable).parent.glob("FreeCAD*.dll"),
                 *Path(sys.executable).parent.glob("TK*.dll")}}}

@lru_cache(maxsize=1)
def _font_identity():
    import matplotlib.font_manager as fm
    paths={Path(font.fname) for font in (*fm.fontManager.ttflist,*fm.fontManager.afmlist)}
    return {str(p):AC.digest(p.read_bytes()) for p in sorted(paths)}

def _picture_runtime():
    import matplotlib
    matplotlib.use("Agg")
    import numpy
    return {"python":sys.version,"matplotlib":matplotlib.__version__,"numpy":numpy.__version__,
            "rc":{k:str(v) for k,v in matplotlib.rcParams.items()},"fonts":_font_identity()}


#: Named views: elevation and azimuth of the orthographic camera.
VIEWS: dict[str, tuple[float, float]] = {
    "iso": (24.0, -28.0),
    "rear_iso": (24.0, 152.0),
    "front": (8.0, 0.0),
    "left": (8.0, 90.0),
    "top": (89.9, -90.0),
    "bottom": (-89.9, -90.0),
    "under_iso": (-32.0, -28.0),
}

HIGHLIGHT = "#e63946"
BLOCKING = "#b3261e"
CONTEXT = "#9aa5b1"
NEW_ALPHA = 0.98
CONTEXT_ALPHA = 0.22


@dataclass(frozen=True)
class Body:
    """``shape`` is a FreeCAD shape or an already triangulated list of
    triangles (see ``triangulate``), so a body drawn in many pictures is
    tessellated once."""
    name: str
    shape: object
    color: str = CONTEXT
    alpha: float = CONTEXT_ALPHA


def triangulate(shape, tolerance: float = 1.0) -> list[list[tuple[float, float, float]]]:
    """Triangles of a FreeCAD shape as plain tuples; a list passes through."""
    if isinstance(shape, list):
        return shape
    def build():
        work=shape.copy(True,False) if hasattr(shape,"copy") else shape
        vertices, faces = work.tessellate(tolerance)
        xyz = [(v.x, v.y, v.z) for v in vertices]
        return [[xyz[i] for i in face] for face in faces]
    if not hasattr(shape,"exportBrepToString"):
        return build()
    clean=shape.cleaned() if hasattr(shape,"cleaned") else shape
    brep=clean.exportBrepToString()
    if isinstance(brep,str):brep=brep.encode("utf8")
    raw=_CACHE.get("triangles",{"brep":AC.digest(brep),"tolerance":tolerance,
        "runtime":_mesh_runtime(),"producer":_producer()},lambda:AC.encode(build()))
    return [[tuple(p) for p in tri] for tri in json.loads(raw)]


def _render_uncached(path: Path, bodies: list[Body], *, title: str, view: str = "iso",
           limits=None, footer: str = "", ground_z: float | None = None, labels: dict[str, str] | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    elev, azim = VIEWS[view]
    fig = plt.figure(figsize=(12, 8), dpi=120)
    ax = fig.add_subplot(111, projection="3d", proj_type="ortho")
    points = []
    # Context first, highlighted bodies last so they are drawn on top.
    for body in sorted(bodies, key=lambda b: b.alpha):
        triangles = triangulate(body.shape)
        if not triangles:
            continue
        # Context bodies are drawn without edges: a translucent wireframe of
        # every triangle hides the one body the picture is about.
        edge = "#1f2933" if body.alpha >= 0.5 else "none"
        ax.add_collection3d(Poly3DCollection(triangles, facecolor=body.color,
                                             edgecolor=edge, linewidth=0.08,
                                             alpha=body.alpha))
        points.extend(p for tri in triangles for p in tri)
    if ground_z is not None:
        x0, x1, y0, y1 = -120, 90, -105, 105
        ax.plot_surface(np.array([[x0, x1], [x0, x1]], dtype=float),
                        np.array([[y0, y0], [y1, y1]], dtype=float),
                        np.array([[ground_z] * 2] * 2, dtype=float),
                        color="#d7e3ed", alpha=0.28, linewidth=0)
        points.extend(((x0, y0, ground_z), (x1, y1, ground_z)))
    if limits is None and points:
        xs, ys, zs = zip(*points)
        pad = 6.0
        limits = ((min(xs) - pad, max(xs) + pad), (min(ys) - pad, max(ys) + pad),
                  (min(zs) - pad, max(zs) + pad))
    if limits:
        ax.set_xlim(*limits[0])
        ax.set_ylim(*limits[1])
        ax.set_zlim(*limits[2])
        ax.set_box_aspect(tuple(hi - lo for lo, hi in limits))
    ax.view_init(elev=elev, azim=azim)
    if labels:
        # Project the callout anchors after setting the camera. Text in 3D can
        # disappear behind the very surface it identifies.
        from mpl_toolkits.mplot3d import proj3d
        for number, body in enumerate(bodies):
            if body.name in labels:
                vertices = [p for tri in triangulate(body.shape) for p in tri]
                if vertices:
                    xyz = tuple((min(a) + max(a)) / 2 for a in zip(*vertices))
                    x, y, _ = proj3d.proj_transform(*xyz, ax.get_proj())
                    left = number % 2 == 0
                    ax.annotate(labels[body.name], (x, y), xytext=(.08 if left else .92, .92-(number//2)*.22),
                        textcoords='axes fraction', ha='center', color='#111', fontsize=12, weight='bold',
                        bbox=dict(facecolor='white', alpha=.95, edgecolor='#333', boxstyle='round'),
                        arrowprops=dict(arrowstyle='->', color='#333', linewidth=1.2))
    ax.set_xlabel("X вперёд, мм")
    ax.set_ylabel("Y влево, мм")
    ax.set_zlabel("Z от пола, мм")
    ax.set_title(title, fontsize=14, weight="bold")
    ax.grid(True, alpha=0.25)
    if labels:
        ax.set_axis_off()
    if footer:
        fig.text(0.02, 0.02, footer, fontsize=8, color="#4b5563")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", facecolor="#f5f7fa",
                metadata={"Software": None})
    plt.close(fig)
    return path

def render(path: Path, bodies: list[Body], *, title: str, view: str = "iso",
           limits=None, footer: str = "", ground_z: float | None = None,
           labels: dict[str, str] | None = None) -> Path:
    triangles=[Body(b.name,triangulate(b.shape),b.color,b.alpha) for b in bodies]
    options=dict(title=title,view=view,limits=limits,footer=footer,ground_z=ground_z,labels=labels)
    inputs=dict(bodies=[dict(name=b.name,triangles=b.shape,color=b.color,alpha=b.alpha) for b in triangles],
        options=options,runtime=_picture_runtime(),producer=_producer())
    def build():
        with tempfile.TemporaryDirectory(prefix="engineering-picture-") as directory:
            target=Path(directory)/"picture.png"
            _render_uncached(target,triangles,**options)
            return target.read_bytes()
    data=_CACHE.get("picture",inputs,build)
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if not path.is_file() or path.read_bytes()!=data:path.write_bytes(data)
    return path
