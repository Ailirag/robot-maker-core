"""Bounded B-Rep screening of small sections and incomplete attachment footprints.

Findings locate geometry to review; SCREENED is not a strength or print approval.
Cross sections use world axes, face-bound intervals and a maximum regular spacing.
Contact rays sample a declared pair of pre-union features in the final part datum.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

MAX_PLANES = 4096
MAX_RAYS = 65536
SECTION_SPACING_MM = 2.0
SMALL_SECTION_MM2 = 6.0
CONTACT_PITCH_MM = 0.25
MIN_CONTACT_DEPTH_MM = 0.5
MIN_CONTACT_FRACTION = 0.95


def _solid(shape):
    if shape is None or not shape.is_valid or len(shape.solids()) != 1 or shape.volume <= 1e-8:
        raise ValueError("structure: expected one valid solid")


def _bounds(shape):
    box = shape.bounding_box()
    return [list(box.min), list(box.max)]


def _round(values):
    return [round(v, 6) for v in values]


def _finding(data):
    raw = json.dumps(data, sort_keys=True, allow_nan=False).encode()
    return dict(id=hashlib.sha256(raw).hexdigest()[:20], **data)


def identity():
    import build123d
    import OCP
    return dict(analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                build123d=build123d.__version__, ocp=OCP.__version__)


def scan_sections(shape):
    """Small connected section faces are candidates, not a universal neck proof.

    This deliberately does not call an area threshold a measured material limit.
    Interior features between sampled planes and unfavourable oblique sections can
    escape; declared material corridors and mechanical review remain necessary.
    """
    from build123d import Plane, section
    _solid(shape)
    bounds = _bounds(shape)
    planes = []
    for axis in range(3):
        low, high = bounds[0][axis], bounds[1][axis]
        count = max(1, math.ceil((high-low)/SECTION_SPACING_MM))
        if count > MAX_PLANES:
            raise ValueError("structure: section sampling budget exceeded")
        knots = {round(low+(high-low)*i/count, 6) for i in range(count+1)}
        for face in shape.faces():
            box = _bounds(face)
            knots.update(round(box[i][axis], 6) for i in (0, 1))
        ordered = sorted(knots)
        planes.extend((axis, (a+b)/2) for a,b in zip(ordered, ordered[1:]) if b-a > 1e-5)
    if len(planes) > MAX_PLANES:
        raise ValueError("structure: section sampling budget exceeded")
    findings = []
    for axis, position in planes:
        origin, normal = [0.,0.,0.], [0.,0.,0.]
        origin[axis], normal[axis] = position, 1.
        cut = section(shape, section_by=Plane(origin=origin, z_dir=normal))
        if not cut.is_valid:
            raise ValueError("structure: invalid section")
        for face in cut.faces():
            area = face.area
            if 1e-8 < area < SMALL_SECTION_MM2:
                findings.append(_finding(dict(kind="SMALL_SECTION", axis=axis,
                    position_mm=round(position,6), area_mm2=round(area,6),
                    bounds_mm=[_round(v) for v in _bounds(face)])))
    findings.sort(key=lambda f:f["id"])
    return dict(schema="structural-section-scan-v1", identity=identity(),
        status="REVIEW_REQUIRED" if findings else "SCREENED", findings=findings,
        sampled_planes=len(planes), policy=dict(max_spacing_mm=SECTION_SPACING_MM,
            small_section_mm2=SMALL_SECTION_MM2, max_planes=MAX_PLANES),
        limitation="Sampled world-axis sections; not exhaustive local thickness, connectivity or strength analysis.",
        strength_n=None, obtained_by="Requires qualified material/process data and load cases or physical testing.",
        physical_approval=False)


def contact_region(parent, attachment):
    from build123d import Compound
    _solid(parent)
    _solid(attachment)
    return Compound(children=list(parent.intersect(attachment) or []))


def scan_contact(parent, attachment, *, axis):
    """Sample the rectangular overlap envelope perpendicular to the joining axis.

    Both features must be declared in the same final part coordinate system.
    A ray counts only a continuous material interval at least MIN_CONTACT_DEPTH_MM
    long. The result is sampled coverage, not an exact area or adhesion proof.
    """
    from build123d import Edge
    if type(axis) is not int or axis not in (0,1,2):
        raise ValueError("structure: contact axis must be 0, 1 or 2")
    common = contact_region(parent, attachment)
    pb, ab = _bounds(parent), _bounds(attachment)
    transverse = [i for i in range(3) if i != axis]
    spans = [(max(pb[0][i],ab[0][i]),min(pb[1][i],ab[1][i])) for i in transverse]
    if any(b-a <= 1e-8 for a,b in spans):
        raise ValueError("structure: empty intended contact projection")
    counts = [max(1,math.ceil((b-a)/CONTACT_PITCH_MM)) for a,b in spans]
    if counts[0]*counts[1] > MAX_RAYS:
        raise ValueError("structure: contact sampling budget exceeded")
    hit = 0
    adequate = 0
    depths = []
    for u in range(counts[0]):
        for v in range(counts[1]):
            depth = 0.
            if common.volume > 1e-8:
                start, end = [0.,0.,0.], [0.,0.,0.]
                start[axis] = min(pb[0][axis],ab[0][axis])-1
                end[axis] = max(pb[1][axis],ab[1][axis])+1
                for i, index in enumerate((u,v)):
                    a,b = spans[i]
                    start[transverse[i]] = end[transverse[i]] = a+(b-a)*(index+.5)/counts[i]
                segments = Edge.make_line(start,end).intersect(common) or []
                depth = max((edge.length for item in segments for edge in item.edges()), default=0.)
            depths.append(depth)
            hit += depth > 1e-7
            adequate += depth >= MIN_CONTACT_DEPTH_MM-1e-7
    fraction = hit/len(depths)
    data = dict(kind="PARTIAL_CONTACT",axis=axis,
        expected_projection_mm2=round(math.prod(b-a for a,b in spans),6),
        projection_bounds_mm=[_round(s) for s in spans],
        covered_fraction=round(fraction,6),
        adequate_depth_fraction=round(adequate/len(depths),6), rays=len(depths),
        min_sampled_depth_mm=round(min(depths),6),
        overlap_mm3=round(common.volume,6),
        status="SCREENED" if adequate/len(depths) >= MIN_CONTACT_FRACTION else "REVIEW_REQUIRED")
    return dict(_finding(data), schema="structural-contact-scan-v1",
        method="sampled_projection_with_minimum_depth",
        policy=dict(max_pitch_mm=CONTACT_PITCH_MM,min_depth_mm=MIN_CONTACT_DEPTH_MM,
                    min_covered_fraction=MIN_CONTACT_FRACTION,max_rays=MAX_RAYS),
        limitation="Rectangular intended projection; narrow gaps between rays may escape. Intent is declared, not inferred from a fused solid.",
        strength_n=None, obtained_by="Qualified load/material evidence or physical testing required.",
        physical_approval=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step", type=Path, required=True)
    parser.add_argument("--part", help="Exact top-level STEP body label; required for multi-body files")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from build123d import import_step
    shape = import_step(args.step)
    if args.part:
        matches = [s for s in shape.children if s.label == args.part]
        if len(matches) != 1:
            raise ValueError("structure: missing or ambiguous STEP label")
        shape = matches[0]
    result = scan_sections(shape)
    result["step_sha256"] = hashlib.sha256(args.step.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8",newline="\n") as handle:
        json.dump(result,handle,ensure_ascii=False,indent=2,allow_nan=False)
        handle.write("\n")
    print(result["status"]+": "+str(len(result["findings"]))+" findings; "+str(args.output))
    return 2 if result["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
