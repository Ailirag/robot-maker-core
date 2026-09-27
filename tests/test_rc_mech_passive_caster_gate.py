"""RED→GREEN gate tests for the selected passive swivel-caster cartridge."""

import unittest

from engineering.passive_caster_gate import (
    PassiveCasterGate,
    UnpoweredDryFitEvidence,
    assess_passive_caster_gate,
)


GEOMETRY_INPUTS = {
    "wheel_loaded_radius_mm": 18.0,
    "wheel_width_mm": 8.0,
    "axle_diameter_mm": 3.0,
    "installed_height_mount_to_floor_mm": 27.0,
    "swivel_trail_mm": 6.0,
    "swivel_swept_radius_mm": 24.0,
}
STABILITY_INPUTS = {
    "total_mass_g": 1000.0,
    "com_x_mm": -20.0,
    "com_y_mm": 0.0,
    "com_height_above_floor_mm": 35.0,
}


class PassiveCasterGateTests(unittest.TestCase):
    def test_missing_measurements_fail_closed_before_source_design(self):
        result = assess_passive_caster_gate({})

        self.assertEqual("BLOCKED_MEASUREMENTS", result.source_design_status)
        self.assertEqual("BLOCKED_PHYSICAL_VALIDATION", result.release_status)
        self.assertFalse(result.cad_generation_allowed)
        self.assertFalse(result.powered_motion_allowed)
        self.assertEqual(
            (
                "wheel_loaded_radius_mm",
                "wheel_width_mm",
                "axle_diameter_mm",
                "installed_height_mount_to_floor_mm",
                "swivel_trail_mm",
                "swivel_swept_radius_mm",
                "total_mass_g",
                "com_x_mm",
                "com_y_mm",
                "com_height_above_floor_mm",
            ),
            result.missing_measurements,
        )
        self.assertEqual(
            "PASSIVE_CASTER_GATE_ONLY_NOT_PRINT_OR_MOTION_AUTHORITY",
            result.authority,
        )

    def test_geometry_inputs_allow_source_design_but_not_release(self):
        result = assess_passive_caster_gate(GEOMETRY_INPUTS)

        self.assertEqual("READY_FOR_SOURCE_DESIGN", result.source_design_status)
        self.assertEqual("BLOCKED_PHYSICAL_VALIDATION", result.release_status)
        self.assertEqual(tuple(STABILITY_INPUTS), result.missing_measurements)
        self.assertFalse(result.cad_generation_allowed)
        self.assertFalse(result.powered_motion_allowed)

    def test_complete_measurements_still_require_unpowered_dry_fit(self):
        result = assess_passive_caster_gate(GEOMETRY_INPUTS | STABILITY_INPUTS)

        self.assertEqual("READY_FOR_SOURCE_DESIGN", result.source_design_status)
        self.assertEqual("BLOCKED_UNPOWERED_DRY_FIT", result.release_status)
        self.assertEqual((), result.missing_measurements)
        self.assertFalse(result.cad_generation_allowed)
        self.assertFalse(result.powered_motion_allowed)

    def test_invalid_numeric_measurements_are_reported_fail_closed(self):
        inputs = GEOMETRY_INPUTS | STABILITY_INPUTS | {
            "wheel_loaded_radius_mm": True,
            "axle_diameter_mm": float("nan"),
            "total_mass_g": 0.0,
            "com_height_above_floor_mm": float("inf"),
        }
        result = assess_passive_caster_gate(inputs)

        self.assertEqual(
            (
                "wheel_loaded_radius_mm",
                "axle_diameter_mm",
                "total_mass_g",
                "com_height_above_floor_mm",
            ),
            result.invalid_measurements,
        )
        self.assertEqual("BLOCKED_MEASUREMENTS", result.source_design_status)
        self.assertEqual("BLOCKED_PHYSICAL_VALIDATION", result.release_status)

    def test_authority_flags_cannot_be_overridden_by_constructor(self):
        with self.assertRaises(TypeError):
            PassiveCasterGate(
                source_design_status="READY_FOR_SOURCE_DESIGN",
                release_status="READY",
                missing_measurements=(),
                invalid_measurements=(),
                cad_generation_allowed=True,
            )
        with self.assertRaises(TypeError):
            PassiveCasterGate(
                source_design_status="READY_FOR_SOURCE_DESIGN",
                release_status="READY",
                missing_measurements=(),
                invalid_measurements=(),
                powered_motion_allowed=True,
            )

    def test_plain_mapping_cannot_self_declare_dry_fit(self):
        result = assess_passive_caster_gate(
            GEOMETRY_INPUTS | STABILITY_INPUTS | {"unpowered_dry_fit_passed": True}
        )
        self.assertEqual("BLOCKED_UNPOWERED_DRY_FIT", result.release_status)

    def test_external_dry_fit_evidence_still_cannot_authorize_release(self):
        evidence = UnpoweredDryFitEvidence(
            gate_id="passive-caster-cartridge-measured-swept-envelope-and-unpowered-dry-fit",
            source="MEASUREMENT_AND_BENCH_PLAN.md",
            passed=True,
        )
        result = assess_passive_caster_gate(
            GEOMETRY_INPUTS | STABILITY_INPUTS,
            unpowered_dry_fit_evidence=evidence,
        )

        self.assertEqual("BLOCKED_EXTERNAL_RELEASE_AUTHORITY", result.release_status)
        self.assertFalse(result.cad_generation_allowed)
        self.assertFalse(result.powered_motion_allowed)

    def test_wrong_external_evidence_never_passes_dry_fit_gate(self):
        bad_evidence = (
            UnpoweredDryFitEvidence(
                gate_id="wrong-gate",
                source="MEASUREMENT_AND_BENCH_PLAN.md",
                passed=True,
            ),
            UnpoweredDryFitEvidence(
                gate_id="passive-caster-cartridge-measured-swept-envelope-and-unpowered-dry-fit",
                source="self-declared",
                passed=True,
            ),
            UnpoweredDryFitEvidence(
                gate_id="passive-caster-cartridge-measured-swept-envelope-and-unpowered-dry-fit",
                source="MEASUREMENT_AND_BENCH_PLAN.md",
                passed=False,
            ),
        )
        for evidence in bad_evidence:
            with self.subTest(evidence=evidence):
                result = assess_passive_caster_gate(
                    GEOMETRY_INPUTS | STABILITY_INPUTS,
                    unpowered_dry_fit_evidence=evidence,
                )
                self.assertEqual("BLOCKED_UNPOWERED_DRY_FIT", result.release_status)

    def test_negative_geometry_is_invalid_but_signed_com_coordinates_are_valid(self):
        result = assess_passive_caster_gate(
            GEOMETRY_INPUTS
            | STABILITY_INPUTS
            | {
                "swivel_trail_mm": -1.0,
                "com_x_mm": -20.0,
                "com_y_mm": -5.0,
            }
        )

        self.assertEqual(("swivel_trail_mm",), result.invalid_measurements)
        self.assertNotIn("com_x_mm", result.invalid_measurements)
        self.assertNotIn("com_y_mm", result.invalid_measurements)
        self.assertEqual("BLOCKED_MEASUREMENTS", result.source_design_status)


if __name__ == "__main__":
    unittest.main()
