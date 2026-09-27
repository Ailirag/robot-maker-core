"""Assembly rows of a design read from its exported STEP (process core).

A design authored outside FreeCAD exports an assembly STEP with one labelled
solid per part and per reference; FreeCAD only reads that file here, as the
CAD toolchain contract requires, and the labels become the body names a
step plan uses. A solid FreeCAD reads as invalid is repaired by
``brep_repair`` and the repair is accepted only when its volume equals the
one the design's own report records; otherwise the invalid solid is kept
and the note says so. Nothing is authored or added here: a design that
needs bodies the STEP does not carry adds them in its own assembly script.

Runs under FreeCADCmd only (``render_steps.py`` loads the design's script,
which calls ``rows_from_step``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

VOLUME_TOLERANCE_MM3 = 1.0


def clean_label(label: str) -> str:
    """A STEP product label as a body name: no blanks, no odd characters."""
    label = re.sub(r"\s+", "_", label.strip())
    return re.sub(r"[^0-9A-Za-z_:.\-]", "", label)


def reported_volumes(report_path: Path | None) -> dict[str, float]:
    """Part volumes the design's own report records (``part_evidence``)."""
    if report_path is None:
        return {}
    try:
        data = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: float(row["volume_mm3"]) for name, row in data.get("part_evidence", {}).items()
            if isinstance(row, dict) and "volume_mm3" in row}


def rows_from_step(step_path: Path, report_path: Path | None = None,
                   document_name: str = "step_assembly") -> tuple[list[tuple[str, object]], list[str]]:
    """Labelled solids of the STEP, invalid ones repaired; plus the repair notes.

    Repeated labels get ``_2``, ``_3`` ... in file order, the way FreeCAD
    would number them, so a plan can name every instance."""
    import FreeCAD
    import Import

    from engineering.instructions import brep_repair

    volumes = reported_volumes(report_path)
    notes: list[str] = []
    doc = FreeCAD.newDocument(document_name)
    try:
        Import.insert(str(step_path), doc.Name)
        rows: list[tuple[str, object]] = []
        seen: dict[str, int] = {}
        for obj in doc.Objects:
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull() or not shape.Solids:
                continue
            # Only leaves carry geometry of their own; groups repeat their children.
            if getattr(obj, "Group", None):
                continue
            name = clean_label(obj.Label) or "solid"
            seen[name] = seen.get(name, 0) + 1
            if seen[name] > 1:
                name = f"{name}_{seen[name]}"
            shape = shape.copy()
            if not shape.isValid():
                repaired, why = brep_repair.repair(shape)
                base = re.sub(r"\d+$", "", name)
                expected = volumes.get(name, volumes.get(base))
                if repaired is not shape and expected is not None and \
                        abs(repaired.Volume - expected) > VOLUME_TOLERANCE_MM3:
                    why.append(f"repaired volume {repaired.Volume:.1f} differs from the report's "
                               f"{expected:.1f}; the repair is not used")
                    repaired = shape
                shape = repaired
                notes.append(f"{name}: " + "; ".join(why))
            rows.append((name, shape))
        if not rows:
            raise RuntimeError(f"no solids read from {step_path}")
        return rows, notes
    finally:
        FreeCAD.closeDocument(doc.Name)


def print_rows(rows: list[tuple[str, object]]) -> None:
    """Bounding boxes, for a script run on its own."""
    for name, shape in rows:
        b = shape.BoundBox
        print(f"{name}: x {b.XMin:.1f}..{b.XMax:.1f} y {b.YMin:.1f}..{b.YMax:.1f} "
              f"z {b.ZMin:.1f}..{b.ZMax:.1f}")
