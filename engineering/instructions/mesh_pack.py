"""Compact mesh pack for the step viewer, built from the exported STL files.

STL stores 50 bytes per triangle and repeats every vertex about six times.
The pack shares vertices, keeps coordinates as int16 at a recorded step
(0.01 mm unless the design is wider than int16 allows) and stores indices
as uint16 where a body has fewer than 65 536 vertices. Normals are not
stored: the viewer derives flat normals per face on the GPU, so edges stay
as sharp as with STL. Nothing is compressed, so the bytes are the same on
every machine and the freshness gate can compare them.

Layout, little-endian::

    magic   8 bytes  b"RCMESH1\\0"
    step    float64  millimetres per unit
    count   uint32   bodies
    per body:
      name_len uint16, name utf-8
      vertex_count uint32, index_count uint32, index_width uint8 (2 or 4)
      int16 x3 * vertex_count
      uint16|uint32 * index_count

Built by ``python -m engineering.regen`` (pure Python); no FreeCAD needed.
"""
from __future__ import annotations

import functools
import hashlib
import json
import struct
from pathlib import Path

MAGIC = b"RCMESH1\0"
DEFAULT_STEP_MM = 0.01
INT16_MAX = 32767


def read_stl(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    body = memoryview(data)[84:84 + count * 50]
    # One iter_unpack over the whole file instead of a call per triangle.
    return [(v[3:6], v[6:9], v[9:12]) for v in struct.iter_unpack("<12fH", body)]


def choose_step(bodies: dict) -> float:
    """The finest step from 0.01 mm that keeps every coordinate inside int16."""
    extent = 0.0
    for triangles in bodies.values():
        for tri in triangles:
            for point in tri:
                extent = max(extent, abs(point[0]), abs(point[1]), abs(point[2]))
    step = DEFAULT_STEP_MM
    while extent / step > INT16_MAX:
        step *= 2
    return step


def pack(bodies: dict, step_mm: float | None = None) -> bytes:
    """``bodies`` maps name -> triangles (three points each), in output order."""
    step = step_mm or choose_step(bodies)
    out = [MAGIC, struct.pack("<dI", step, len(bodies))]
    for name, triangles in bodies.items():
        lookup: dict[tuple[int, int, int], int] = {}
        vertices: list[tuple[int, int, int]] = []
        indices: list[int] = []
        for tri in triangles:
            for point in tri:
                key = (round(point[0] / step), round(point[1] / step), round(point[2] / step))
                index = lookup.get(key)
                if index is None:
                    index = len(vertices)
                    lookup[key] = index
                    vertices.append(key)
                indices.append(index)
        width = 2 if len(vertices) <= 0xFFFF else 4
        encoded = name.encode("utf-8")
        out.append(struct.pack("<H", len(encoded)) + encoded)
        out.append(struct.pack("<IIB", len(vertices), len(indices), width))
        out.append(struct.pack(f"<{3 * len(vertices)}h", *(c for v in vertices for c in v)))
        out.append(struct.pack(f"<{len(indices)}{'H' if width == 2 else 'I'}", *indices))
    return b"".join(out)


def unpack(data: bytes) -> dict:
    """Inverse of ``pack``: step and bodies with integer vertices and indices."""
    if data[:8] != MAGIC:
        raise ValueError("not a RCMESH1 pack")
    step, count = struct.unpack_from("<dI", data, 8)
    offset = 20
    bodies = {}
    for _ in range(count):
        (name_len,) = struct.unpack_from("<H", data, offset)
        offset += 2
        name = data[offset:offset + name_len].decode("utf-8")
        offset += name_len
        vertex_count, index_count, width = struct.unpack_from("<IIB", data, offset)
        offset += 9
        flat = struct.unpack_from(f"<{3 * vertex_count}h", data, offset)
        offset += 6 * vertex_count
        fmt = "H" if width == 2 else "I"
        indices = struct.unpack_from(f"<{index_count}{fmt}", data, offset)
        offset += index_count * width
        bodies[name] = {"vertices": [flat[i:i + 3] for i in range(0, len(flat), 3)],
                        "indices": list(indices), "index_width": width}
    return {"step_mm": step, "bodies": bodies}


def build(models_dir: Path) -> bytes:
    """Pack every body listed in the exported ``index.json``, in its order.

    Cached per index digest within a process: the freshness gate and the
    offline file both ask for the same pack, and parsing 10 MB of STL in
    Python is the slow part of a commit."""
    index_bytes = (models_dir / "index.json").read_bytes()
    return _build_cached(str(models_dir.resolve()), hashlib.sha256(index_bytes).hexdigest())


@functools.lru_cache(maxsize=4)
def _build_cached(models_dir: str, index_digest: str) -> bytes:
    index = json.loads((Path(models_dir) / "index.json").read_text(encoding="utf-8"))
    bodies = {body["name"]: read_stl(Path(models_dir) / body["file"]) for body in index["bodies"]}
    return pack(bodies)
