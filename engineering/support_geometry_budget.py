"""Pure-Python support-geometry budgets for RC-MECH-CURRENT-001.

This module evaluates declared layout constraints only.  It does not authorize
CAD release, printing, assembly, power, motion, or a physical stability claim.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import ClassVar


@dataclass(frozen=True)
class DeclaredTrackWindow:
    """Necessary, not sufficient, track window under explicitly modeled limits."""

    mechanical_adjustment_mm: tuple[float, float]
    declared_adjustment_mm: tuple[float, float] | None
    declared_span_mm: float
    clearance_at_mechanical_inboard_end_mm: float
    outer_width_at_mechanical_outboard_end_mm: float
    nominal_satisfies_modeled_limits: bool
    declared_window_is_empty: bool
    mechanical_window_satisfies_modeled_limits: bool
    critical_clearance_breached_at_mechanical_inboard_end: bool
    limits_modeled: tuple[str, ...]
    violations: tuple[str, ...]

    authority: ClassVar[str] = (
        "DECLARED_LAYOUT_LIMITS_ONLY_NOT_FULL_FIT_OR_RELEASE_QUALIFICATION"
    )


@dataclass(frozen=True)
class SupportTriangleSweepBudget:
    """Upper bounds for a symmetric two-wheel plus rear-support sweep."""

    nominal_vertices_mm: tuple[tuple[float, float], ...]
    third_support_x_range_mm: tuple[float, float]
    front_unsupported_plate_overhang_mm: float
    nominal_rear_unsupported_plate_overhang_mm: float
    plate_midpoint_x_mm: float
    drive_axis_ahead_of_plate_midpoint_mm: float
    nominal_inradius_upper_bound_mm: float
    worst_case_inradius_upper_bound_mm: float
    best_case_inradius_upper_bound_mm: float
    worst_case_third_support_x_mm: float
    nominal_incenter_x_mm: float

    authority: ClassVar[str] = (
        "GEOMETRY_UPPER_BOUND_ONLY_NOT_A_STATIC_OR_DYNAMIC_STABILITY_VERDICT"
    )


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _ordered_pair(name: str, values: tuple[float, float]) -> tuple[float, float]:
    if len(values) != 2:
        raise ValueError(f"{name} must contain exactly two endpoints")
    low = _finite(f"{name}[0]", values[0])
    high = _finite(f"{name}[1]", values[1])
    if low > high:
        raise ValueError(f"{name} endpoints must be ordered")
    return low, high


def _triangle_inradius(drive_x: float, half_track: float, third_x: float) -> float:
    span = drive_x - third_x
    equal_side = math.hypot(span, half_track)
    return half_track * span / (half_track + equal_side)


def support_triangle_sweep_budget(
    *,
    drive_axis_x_mm: float,
    half_track_mm: float,
    nominal_third_support_x_mm: float,
    third_support_x_range_mm: tuple[float, float],
    plate_x_range_mm: tuple[float, float],
) -> SupportTriangleSweepBudget:
    """Compute nominal and swept geometric upper bounds for the rear support.

    The calculation intentionally ignores mass, CoM height, braking, slope,
    contact patch and tolerances.  It reports geometry only and cannot produce
    a static or dynamic stability verdict.
    """

    drive_x = _finite("drive_axis_x_mm", drive_axis_x_mm)
    half_track = _finite("half_track_mm", half_track_mm)
    nominal_third_x = _finite("nominal_third_support_x_mm", nominal_third_support_x_mm)
    third_low, third_high = _ordered_pair(
        "third_support_x_range_mm", third_support_x_range_mm
    )
    plate_low, plate_high = _ordered_pair("plate_x_range_mm", plate_x_range_mm)

    if half_track <= 0.0:
        raise ValueError("half_track_mm must be positive")
    if plate_low == plate_high:
        raise ValueError("plate X endpoints must be distinct")
    if not third_low <= nominal_third_x <= third_high:
        raise ValueError("nominal third-support X must lie inside its range")
    if third_high >= drive_x:
        raise ValueError("this budget requires the full third-support sweep behind the drive axis")

    nominal_inradius = _triangle_inradius(drive_x, half_track, nominal_third_x)
    endpoint_budgets = [
        (third_x, _triangle_inradius(drive_x, half_track, third_x))
        for third_x in (third_low, third_high)
    ]
    worst_x, worst_inradius = min(endpoint_budgets, key=lambda item: item[1])
    _, best_inradius = max(endpoint_budgets, key=lambda item: item[1])
    plate_midpoint = (plate_low + plate_high) / 2.0

    return SupportTriangleSweepBudget(
        nominal_vertices_mm=(
            (drive_x, half_track),
            (drive_x, -half_track),
            (nominal_third_x, 0.0),
        ),
        third_support_x_range_mm=(third_low, third_high),
        front_unsupported_plate_overhang_mm=round(max(0.0, plate_high - drive_x), 6),
        nominal_rear_unsupported_plate_overhang_mm=round(
            max(0.0, nominal_third_x - plate_low), 6
        ),
        plate_midpoint_x_mm=round(plate_midpoint, 6),
        drive_axis_ahead_of_plate_midpoint_mm=round(drive_x - plate_midpoint, 6),
        nominal_inradius_upper_bound_mm=round(nominal_inradius, 6),
        worst_case_inradius_upper_bound_mm=round(worst_inradius, 6),
        best_case_inradius_upper_bound_mm=round(best_inradius, 6),
        worst_case_third_support_x_mm=worst_x,
        nominal_incenter_x_mm=round(drive_x - nominal_inradius, 6),
    )


def declared_track_adjustment_window(
    *,
    nominal_half_track_mm: float,
    wheel_width_mm: float,
    outer_width_limit_mm: float,
    wheel_well_inner_edge_y_mm: float,
    dynamic_clearance_mm: float,
    critical_clearance_mm: float,
    mechanical_adjustment_mm: tuple[float, float],
) -> DeclaredTrackWindow:
    """Intersect mechanical track travel with declared clearance/width limits.

    Positive adjustment moves each wheel outboard; negative moves it inboard.
    The result is necessary but not sufficient: pod/body/battery projections,
    tolerances and physical dry-fit remain outside this model.
    """

    half_track = _finite("nominal_half_track_mm", nominal_half_track_mm)
    wheel_width = _finite("wheel_width_mm", wheel_width_mm)
    width_limit = _finite("outer_width_limit_mm", outer_width_limit_mm)
    well_edge = _finite("wheel_well_inner_edge_y_mm", wheel_well_inner_edge_y_mm)
    dynamic_clearance = _finite("dynamic_clearance_mm", dynamic_clearance_mm)
    critical_clearance = _finite("critical_clearance_mm", critical_clearance_mm)
    mechanical_low, mechanical_high = _ordered_pair(
        "mechanical_adjustment_mm", mechanical_adjustment_mm
    )

    if half_track <= 0.0:
        raise ValueError("nominal_half_track_mm must be positive")
    if wheel_width <= 0.0:
        raise ValueError("wheel_width_mm must be positive")
    if width_limit <= 0.0:
        raise ValueError("outer_width_limit_mm must be positive")
    if well_edge < 0.0:
        raise ValueError("wheel_well_inner_edge_y_mm cannot be negative")
    if critical_clearance < 0.0 or dynamic_clearance < 0.0:
        raise ValueError("clearance thresholds cannot be negative")
    if critical_clearance > dynamic_clearance:
        raise ValueError("critical clearance cannot exceed dynamic clearance")

    half_wheel = wheel_width / 2.0
    clearance_limited_low = well_edge + dynamic_clearance + half_wheel - half_track
    width_limited_high = width_limit / 2.0 - half_wheel - half_track
    declared_low = max(mechanical_low, clearance_limited_low)
    declared_high = min(mechanical_high, width_limited_high)
    declared_window_is_empty = declared_low > declared_high

    inboard_clearance = half_track + mechanical_low - half_wheel - well_edge
    outboard_width = 2.0 * (half_track + mechanical_high + half_wheel)

    violations: list[str] = []
    if inboard_clearance < dynamic_clearance:
        violations.append(
            "mechanical inboard endpoint violates dynamic wheel-to-plastic clearance"
        )
    if inboard_clearance < critical_clearance:
        violations.append(
            "mechanical inboard endpoint violates critical wheel-to-plastic clearance"
        )
    if outboard_width > width_limit:
        violations.append(
            "mechanical outboard endpoint violates declared overall width limit"
        )
    if declared_window_is_empty:
        violations.append("no mechanical adjustment satisfies all modeled limits")

    if declared_window_is_empty:
        declared_adjustment = None
        declared_span = 0.0
        nominal_satisfies = False
    else:
        declared_adjustment = (round(declared_low, 6), round(declared_high, 6))
        declared_span = round(declared_high - declared_low, 6)
        nominal_satisfies = declared_low <= 0.0 <= declared_high

    return DeclaredTrackWindow(
        mechanical_adjustment_mm=(round(mechanical_low, 6), round(mechanical_high, 6)),
        declared_adjustment_mm=declared_adjustment,
        declared_span_mm=declared_span,
        clearance_at_mechanical_inboard_end_mm=round(inboard_clearance, 6),
        outer_width_at_mechanical_outboard_end_mm=round(outboard_width, 6),
        nominal_satisfies_modeled_limits=nominal_satisfies,
        declared_window_is_empty=declared_window_is_empty,
        mechanical_window_satisfies_modeled_limits=not violations,
        critical_clearance_breached_at_mechanical_inboard_end=(
            inboard_clearance < critical_clearance
        ),
        limits_modeled=(
            "dynamic wheel-to-printed-part clearance",
            "critical wheel-to-plastic clearance",
            "declared overall tire width",
        ),
        violations=tuple(violations),
    )
