"""Assemblability by insertion sweep (FreeCAD only).

A static interference check at the seated position answers "does it fit";
it never answers "can it get there". A part pushed onto a bolted module
must pass every intermediate position without touching the fixed bodies.
This module moves a part along its approach path and records every
interference on the way, so that a design whose seat is clean but whose
path is blocked fails deterministically.

Conventions:

- ``direction`` is the unit vector along which the moving part travels
  INTO its seat (a guard pushed up from the floor: ``(0, 0, 1)``).
- offset ``d`` means the part is displaced by ``-d * direction`` from its
  seated position; ``d = 0`` is the seat, ``d = travel_mm`` is the start.
- volumes are boolean ``common`` volumes in mm3; anything above
  ``tolerance_mm3`` is an interference.

No file is written and nothing is approved here. The result is a report
for a verifier, an evidence record or a test to judge.
"""
from __future__ import annotations

from typing import Iterable, Mapping


def _offsets(travel_mm: float, step_mm: float, extra: Iterable[float]) -> list[float]:
    if travel_mm < 0 or step_mm <= 0:
        raise ValueError("travel_mm must be >= 0 and step_mm > 0")
    count = int(round(travel_mm / step_mm))
    values = {round(i * step_mm, 6) for i in range(count + 1)}
    values.add(round(float(travel_mm), 6))
    values.update(round(float(v), 6) for v in extra)
    return sorted(v for v in values if 0.0 <= v <= travel_mm + 1e-9)


def _moved(shape, dx: float, dy: float, dz: float):
    from FreeCAD import Vector

    copy = shape.copy()
    copy.translate(Vector(dx, dy, dz))
    return copy


def sweep_insertion(moving, obstacles: Mapping[str, object], direction,
                    travel_mm: float, step_mm: float = 1.0,
                    tolerance_mm3: float = 1e-3, extra_offsets: Iterable[float] = ()) -> dict:
    """Report every interference between ``moving`` and ``obstacles`` along the path."""
    dx, dy, dz = (float(c) for c in direction)
    length = (dx * dx + dy * dy + dz * dz) ** 0.5
    if length == 0.0:
        raise ValueError("direction must be a non-zero vector")
    dx, dy, dz = dx / length, dy / length, dz / length
    offsets = _offsets(travel_mm, step_mm, extra_offsets)
    interferences: dict[str, dict[str, float]] = {}
    for offset in offsets:
        placed = _moved(moving, -offset * dx, -offset * dy, -offset * dz)
        hits: dict[str, float] = {}
        for name, obstacle in obstacles.items():
            volume = placed.common(obstacle).Volume
            if volume > tolerance_mm3:
                hits[name] = round(volume, 3)
        if hits:
            interferences[f"{offset:g}"] = dict(sorted(hits.items()))
    blocked = sorted((float(k) for k in interferences), reverse=False)
    bodies = sorted({name for hits in interferences.values() for name in hits})
    return {
        "direction": [round(dx, 6), round(dy, 6), round(dz, 6)],
        "travel_mm": float(travel_mm),
        "step_mm": float(step_mm),
        "tolerance_mm3": float(tolerance_mm3),
        "offsets_checked": len(offsets),
        "interferences_by_offset_mm": interferences,
        "first_blocked_offset_mm": (max(blocked) if blocked else None),
        "seated_interference_mm3": interferences.get("0", {}),
        "blocking_bodies": bodies,
        "assemblable": not interferences,
    }


def seated_clearances(moving, obstacles: Mapping[str, object]) -> dict[str, float]:
    """Minimum distance from the seated part to every obstacle, in mm."""
    return {name: round(moving.distToShape(obstacle)[0], 3)
            for name, obstacle in obstacles.items()}
