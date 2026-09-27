"""Repair of solids that FreeCAD reads from a STEP as invalid because a planar
face carries a hole whose wire touches the face's outer wire.

build123d writes a corner boss that touches two walls as a hole in the wall
face, and that hole is tangent to the face boundary. OCC 7.8 inside FreeCAD
flags the face ("Intersecting wires"): every boolean against the solid then
returns garbage, its tessellation is broken, and ``Volume`` is wrong, while
build123d's own ``is_valid`` had passed. The repair keeps every other face,
rebuilds the bad face with an outer wire notched around the tangent holes (a
2D cut instead of a hole) and re-sews the shell. Topology changes, geometry
does not: the caller compares the repaired volume with the one the design's
report records, and that equality is the proof the repair is faithful.

FreeCAD only; nothing here is imported by the stdlib gates.
"""
from __future__ import annotations

TOLERANCE = 1e-3


def invalid_faces(shape) -> list[int]:
    """Indices of the faces FreeCAD reports invalid."""
    out = []
    for index, face in enumerate(shape.Faces):
        try:
            ok = face.isValid()
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            out.append(index)
    return out


def _touches(inner, outer) -> bool:
    """Does the hole's bounding box reach the outer wire's, on an axis the
    planar face actually spans (a planar face is flat on one axis)?"""
    a, b = inner.BoundBox, outer.BoundBox
    spans = ((a.XMin, a.XMax, b.XMin, b.XMax), (a.YMin, a.YMax, b.YMin, b.YMax),
             (a.ZMin, a.ZMax, b.ZMin, b.ZMax))
    for lo, hi, outer_lo, outer_hi in spans:
        if outer_hi - outer_lo < TOLERANCE:
            continue
        if abs(lo - outer_lo) < TOLERANCE or abs(hi - outer_hi) < TOLERANCE:
            return True
    return False


def rebuild_face(face):
    """The same planar face with its tangent holes turned into boundary notches."""
    import Part

    outer = face.OuterWire
    tangent, holes = [], []
    for wire in face.Wires:
        if wire.isSame(outer):
            continue
        (tangent if _touches(wire, outer) else holes).append(wire)
    if not tangent:
        return None, 0
    rebuilt = Part.Face(outer)
    for wire in tangent + holes:
        cut = rebuilt.cut(Part.Face(wire))
        if not cut.Faces:
            return None, len(tangent)
        rebuilt = cut.Faces[0]
    return (rebuilt if rebuilt.isValid() else None), len(tangent)


def repair(shape):
    """``(solid, notes)``: the repaired solid, or the input when nothing could be
    done. ``notes`` says what was rebuilt so the caller can record it."""
    import Part

    bad = invalid_faces(shape)
    if not bad:
        return shape, []
    notes: list[str] = []
    faces = list(shape.Faces)
    for index in bad:
        rebuilt, count = rebuild_face(faces[index])
        if rebuilt is None:
            return shape, [f"face {index} invalid and not repairable"]
        faces[index] = rebuilt
        notes.append(f"face {index}: {count} tangent hole(s) turned into boundary notches")
    sewn = Part.makeCompound(faces)
    sewn.sewShape()
    try:
        solid = Part.Solid(sewn.Shells[0] if sewn.Shells else Part.Shell(sewn.Faces))
    except Exception as exc:  # noqa: BLE001
        return shape, notes + [f"sewing failed: {exc!r}"]
    if not solid.isValid():
        return shape, notes + ["rebuilt solid still invalid"]
    return solid, notes + [f"volume {shape.Volume:.1f} -> {solid.Volume:.1f} mm3"]
