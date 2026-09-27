"""Рендер каждой печатной детали в PNG, чистым Python.

Ста девяти деталям на диске соответствовало девять превью: увидеть деталь
можно было только открыв CAD. Это и читается как «нет систематизации».

Рисуется без FreeCAD и без сторонних библиотек, ровно тем же приёмом, что и
превью плит: пиксельный буфер и PNG через zlib. Значит рендер получается на
любой машине, где есть Python, и не зависит от того, установлен ли CAD.

Картинки кладутся в `web/renders/`, а НЕ рядом с STL: STL лежат внутри
`out_candidate_*`, а эти каталоги неизменны после записи.

    python engineering/render_parts.py
    python engineering/render_parts.py --only cliff_holder
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / "web" / "renders"

WIDTH, HEIGHT = 480, 360
MARGIN = 18
BACKGROUND = (250, 250, 250)
#: Направление взгляда: три четверти сверху. Углы подобраны так, чтобы у
#: плоских деталей была видна толщина, а у высоких — обе стенки.
YAW_DEG, PITCH_DEG = 35.0, 28.0
LIGHT = (0.35, -0.45, 0.82)
BASE_COLOR = (86, 122, 168)
EDGE_DARKEN = 0.55


def read_stl(path):
    data = path.read_bytes()
    if len(data) < 84:
        return []
    count = struct.unpack("<I", data[80:84])[0]
    if 84 + count * 50 != len(data):
        return []                      # не бинарный STL — пропускаем молча
    out = []
    offset = 84
    for _ in range(count):
        verts = []
        for index in range(3):
            base = offset + 12 + index * 12
            verts.append(struct.unpack("<fff", data[base:base + 12]))
        out.append(verts)
        offset += 50
    return out


def rotated(triangles):
    """Поворот на yaw вокруг Z и pitch вокруг X, одной матрицей."""
    cy, sy = math.cos(math.radians(YAW_DEG)), math.sin(math.radians(YAW_DEG))
    cp, sp = math.cos(math.radians(PITCH_DEG)), math.sin(math.radians(PITCH_DEG))
    out = []
    for tri in triangles:
        moved = []
        for x, y, z in tri:
            rx = x * cy - y * sy
            ry = x * sy + y * cy
            moved.append((rx, ry * cp - z * sp, ry * sp + z * cp))
        out.append(moved)
    return out


def normal(tri):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return None
    return nx / length, ny / length, nz / length


def png(path, pixels, width, height):
    """PNG без сторонних библиотек: сырые строки, zlib, три чанка."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                  # фильтр строки: none
        row = 3 * y * width
        raw += pixels[row:row + 3 * width]

    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack(">I", len(payload)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
                     + chunk(b"IEND", b""))


def render(triangles, path):
    tris = rotated(triangles)
    xs = [v[0] for t in tris for v in t]
    ys = [v[2] for t in tris for v in t]
    if not xs:
        return False
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    span_x = max(hi_x - lo_x, 1e-6)
    span_y = max(hi_y - lo_y, 1e-6)
    scale = min((WIDTH - 2 * MARGIN) / span_x, (HEIGHT - 2 * MARGIN) / span_y)
    off_x = (WIDTH - span_x * scale) / 2.0 - lo_x * scale
    off_y = (HEIGHT - span_y * scale) / 2.0 - lo_y * scale

    pixels = bytearray(bytes(BACKGROUND) * (WIDTH * HEIGHT))
    depth = [1e18] * (WIDTH * HEIGHT)

    for tri in tris:
        norm = normal(tri)
        if norm is None:
            continue
        shade = abs(norm[0] * LIGHT[0] + norm[1] * LIGHT[1] + norm[2] * LIGHT[2])
        shade = EDGE_DARKEN + (1.0 - EDGE_DARKEN) * shade
        colour = tuple(min(255, int(c * shade)) for c in BASE_COLOR)

        pts = [(v[0] * scale + off_x, HEIGHT - (v[2] * scale + off_y), v[1])
               for v in tri]
        min_px = max(0, int(min(p[0] for p in pts)))
        max_px = min(WIDTH - 1, int(max(p[0] for p in pts)) + 1)
        min_py = max(0, int(min(p[1] for p in pts)))
        max_py = min(HEIGHT - 1, int(max(p[1] for p in pts)) + 1)
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = pts
        area = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(area) < 1e-12:
            continue

        for py in range(min_py, max_py + 1):
            for px in range(min_px, max_px + 1):
                wa = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / area
                wb = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / area
                wc = 1.0 - wa - wb
                if wa < -1e-9 or wb < -1e-9 or wc < -1e-9:
                    continue
                z = wa * z0 + wb * z1 + wc * z2
                cell = py * WIDTH + px
                if z >= depth[cell]:
                    continue
                depth[cell] = z
                pixels[3 * cell:3 * cell + 3] = bytes(colour)

    png(path, pixels, WIDTH, HEIGHT)
    return True


def parts():
    """Все печатные STL, кроме плит и сборок: те уже имеют свои превью."""
    out = []
    for path in sorted((REPO / "cad" / "prototypes").rglob("*.stl")):
        rel = str(path.relative_to(REPO)).replace("\\", "/")
        if "/stl/" not in rel:
            continue
        out.append(path)
    return out


def main(argv=()):
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    OUT.mkdir(parents=True, exist_ok=True)
    made, skipped = 0, 0
    for path in parts():
        if only and only not in path.stem:
            continue
        target = OUT / (path.stem + ".png")
        if target.exists():
            skipped += 1
            continue
        triangles = read_stl(path)
        if not triangles:
            print("  ПРОПУСК (не бинарный STL): %s" % path.name)
            continue
        if render(triangles, target):
            made += 1
            print("  %-52s %6d треуг. -> %s"
                  % (path.stem[:52], len(triangles), target.name))
    print()
    print("  нарисовано %d, уже было %d" % (made, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
