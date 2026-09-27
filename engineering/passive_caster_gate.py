"""Fail-closed input gate for the selected passive swivel-caster cartridge.

The gate can declare source-design inputs complete.  It cannot authorize CAD
generation, printing, assembly, powered tests, motion, or release.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import ClassVar, Mapping


GEOMETRY_MEASUREMENTS = (
    "wheel_loaded_radius_mm",
    "wheel_width_mm",
    "axle_diameter_mm",
    "installed_height_mount_to_floor_mm",
    "swivel_trail_mm",
    "swivel_swept_radius_mm",
)

STABILITY_MEASUREMENTS = (
    "total_mass_g",
    "com_x_mm",
    "com_y_mm",
    "com_height_above_floor_mm",
)

REQUIRED_MEASUREMENTS = GEOMETRY_MEASUREMENTS + STABILITY_MEASUREMENTS


@dataclass(frozen=True)
class PassiveCasterGate:
    source_design_status: str
    release_status: str
    missing_measurements: tuple[str, ...]
    invalid_measurements: tuple[str, ...]

    cad_generation_allowed: ClassVar[bool] = False
    powered_motion_allowed: ClassVar[bool] = False
    authority: ClassVar[str] = "PASSIVE_CASTER_GATE_ONLY_NOT_PRINT_OR_MOTION_AUTHORITY"


@dataclass(frozen=True)
class UnpoweredDryFitEvidence:
    """Externally recorded dry-fit observation; never a release authority."""

    gate_id: str
    source: str
    passed: bool


EXPECTED_DRY_FIT_GATE_ID = (
    "passive-caster-cartridge-measured-swept-envelope-and-unpowered-dry-fit"
)
EXPECTED_DRY_FIT_SOURCE = "MEASUREMENT_AND_BENCH_PLAN.md"


def _valid_measurement(name: str, value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    if not math.isfinite(numeric):
        return False
    if name == "com_y_mm" or name == "com_x_mm":
        return True
    return numeric > 0.0


def assess_passive_caster_gate(
    inputs: Mapping[str, object],
    *,
    unpowered_dry_fit_evidence: UnpoweredDryFitEvidence | None = None,
) -> PassiveCasterGate:
    """Report whether measured inputs are complete, without granting authority."""

    missing = tuple(name for name in REQUIRED_MEASUREMENTS if name not in inputs)
    invalid = tuple(
        name
        for name in REQUIRED_MEASUREMENTS
        if name in inputs and not _valid_measurement(name, inputs[name])
    )
    unavailable = set(missing) | set(invalid)
    geometry_blocked = any(name in unavailable for name in GEOMETRY_MEASUREMENTS)

    source_design_status = (
        "BLOCKED_MEASUREMENTS" if geometry_blocked else "READY_FOR_SOURCE_DESIGN"
    )

    evidence_is_valid = (
        isinstance(unpowered_dry_fit_evidence, UnpoweredDryFitEvidence)
        and unpowered_dry_fit_evidence.gate_id == EXPECTED_DRY_FIT_GATE_ID
        and unpowered_dry_fit_evidence.source == EXPECTED_DRY_FIT_SOURCE
        and unpowered_dry_fit_evidence.passed is True
    )

    if unavailable:
        release_status = "BLOCKED_PHYSICAL_VALIDATION"
    elif not evidence_is_valid:
        release_status = "BLOCKED_UNPOWERED_DRY_FIT"
    else:
        release_status = "BLOCKED_EXTERNAL_RELEASE_AUTHORITY"

    return PassiveCasterGate(
        source_design_status=source_design_status,
        release_status=release_status,
        missing_measurements=missing,
        invalid_measurements=invalid,
    )
