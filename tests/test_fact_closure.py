"""A candidate pins the facts it consumed, not the bytes of the fact manifest.

The byte pin was the right idea with the wrong grain: one measurement recorded
anywhere in ``cad/prototype_parameters.json`` invalidated every candidate that
had ever read the file, including candidates that never used the changed fact.
The closure compares exactly the records a candidate consumed between the image
it was generated from and the working tree, and reports which one moved.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engineering import fact_closure as FC  # noqa: E402


def manifest(**parameters):
    return json.dumps({"schema": "x", "parameters": parameters},
                      ensure_ascii=False, indent=1).encode("utf-8")


A = {"value": 45.0, "unit": "mm", "provenance": "MEASURED", "source": "WHEEL"}
B = {"value": 1.0, "unit": "mm", "provenance": "MEASURED", "source": "GAP"}


class TestClosureComparison(unittest.TestCase):
    def test_identical_images_have_no_problems(self):
        self.assertEqual([], FC.closure_problems(manifest(a=A, b=B), manifest(a=A, b=B), ("a",)))

    def test_a_change_outside_the_closure_is_not_a_problem(self):
        changed = dict(B, value=2.0)
        self.assertEqual([], FC.closure_problems(manifest(a=A, b=B), manifest(a=A, b=changed), ("a",)))

    def test_a_changed_consumed_value_is_named(self):
        changed = dict(A, value=46.0)
        problems = FC.closure_problems(manifest(a=A, b=B), manifest(a=changed, b=B), ("a",))
        self.assertEqual(1, len(problems))
        self.assertIn("a", problems[0])
        self.assertIn("45.0", problems[0])
        self.assertIn("46.0", problems[0])

    def test_a_changed_provenance_or_datum_counts_as_a_change(self):
        """A MEASURED value that became CALCULATED is a different fact."""
        for field, value in (("provenance", "CALCULATED"), ("datum", "other face"),
                             ("unit", "cm"), ("scope", "next-drive")):
            with self.subTest(field=field):
                changed = dict(A)
                changed[field] = value
                problems = FC.closure_problems(manifest(a=A), manifest(a=changed), ("a",))
                self.assertEqual(1, len(problems))
                self.assertIn(field, problems[0])

    def test_a_note_or_comment_field_is_not_a_change(self):
        """Prose next to the number is documentation, not the fact."""
        changed = dict(A, note="re-measured on 2026-09-04, same reading")
        self.assertEqual([], FC.closure_problems(manifest(a=A), manifest(a=changed), ("a",)))

    def test_a_consumed_fact_missing_from_either_image_is_a_problem(self):
        self.assertEqual(1, len(FC.closure_problems(manifest(b=B), manifest(a=A, b=B), ("a",))))
        self.assertEqual(1, len(FC.closure_problems(manifest(a=A, b=B), manifest(b=B), ("a",))))

    def test_an_empty_closure_is_a_problem(self):
        self.assertTrue(FC.closure_problems(manifest(a=A), manifest(a=A), ()))

    def test_an_unreadable_image_is_a_problem_not_a_pass(self):
        self.assertTrue(FC.closure_problems(b"not json", manifest(a=A), ("a",)))
        self.assertTrue(FC.closure_problems(manifest(a=A), b"[]", ("a",)))

    def test_every_consumed_fact_is_reported_not_just_the_first(self):
        changed_a = dict(A, value=46.0)
        changed_b = dict(B, value=2.0)
        problems = FC.closure_problems(manifest(a=A, b=B), manifest(a=changed_a, b=changed_b), ("a", "b"))
        self.assertEqual(2, len(problems))


class TestComparedFields(unittest.TestCase):
    def test_the_compared_fields_are_the_ones_engineering_facts_resolves(self):
        """``engineering.facts`` reads value, unit, provenance, source, datum/basis
        and scope/revision. The closure must compare the same set, or a change
        the fact resolver would see could pass the closure."""
        self.assertEqual(
            {"value", "unit", "provenance", "source", "datum", "basis", "scope", "revision"},
            set(FC.COMPARED_FIELDS))


if __name__ == "__main__":
    unittest.main()


class RecordedFactsAreComparedWithoutASnapshot(unittest.TestCase):
    """Evidence that wrote down the facts it read can be checked against the live
    manifest directly: no snapshot of the old file, no re-run of the study.

    Fail closed. An empty record set closes nothing. A record set that disagrees
    with what the producing code declares it consumes means the evidence is
    silently narrower than the code, and the check would then pass a change it
    should refuse. A compared field the producer never wrote down cannot be
    compared, so it is a problem until the call site declares that this producer
    never records it.
    """

    LIVE = {"parameters": {
        "a_mm": {"value": 1.0, "unit": "mm", "provenance": "MEASURED", "source": "s",
                 "datum": None, "basis": None, "scope": None, "revision": None},
        "b_mm": {"value": 2.0, "unit": "mm", "provenance": "MEASURED", "source": "s",
                 "datum": None, "basis": None, "scope": None, "revision": None},
        "spare_mm": {"value": 9.0, "unit": "mm", "provenance": "UNKNOWN", "source": "s"},
    }}

    def live(self, **changes):
        import copy
        data = copy.deepcopy(self.LIVE)
        for fact_id, fields in changes.items():
            if fields is None:
                data["parameters"].pop(fact_id, None)
            else:
                data["parameters"].setdefault(fact_id, {}).update(fields)
        return json.dumps(data).encode()

    def recorded(self, *ids, omit=()):
        from engineering import fact_closure as F
        table = F.fact_table(self.live())
        return {i: {k: v for k, v in F.fact_record(table, i).items() if k not in omit} for i in ids}

    def test_unchanged_consumed_facts_report_nothing(self):
        from engineering import fact_closure as F
        self.assertEqual(F.verify_recorded_facts(self.recorded("a_mm", "b_mm"), self.live()), [])

    def test_a_moved_consumed_fact_is_named_with_its_field(self):
        from engineering import fact_closure as F
        problems = F.verify_recorded_facts(self.recorded("a_mm"), self.live(a_mm={"value": 5.0}))
        self.assertEqual(len(problems), 1)
        self.assertIn("a_mm", problems[0])
        self.assertIn("value", problems[0])

    def test_a_downgraded_provenance_is_caught(self):
        from engineering import fact_closure as F
        problems = F.verify_recorded_facts(self.recorded("a_mm"), self.live(a_mm={"provenance": "UNKNOWN"}))
        self.assertTrue(problems and "provenance" in problems[0])

    def test_an_unrelated_fact_is_not_this_artifact_s_business(self):
        from engineering import fact_closure as F
        for change in ({"spare_mm": {"value": 42.0}}, {"new_fact_mm": {"value": 1.0, "unit": "mm"}}):
            with self.subTest(change=sorted(change)):
                self.assertEqual(F.verify_recorded_facts(self.recorded("a_mm"), self.live(**change)), [])

    def test_an_empty_record_set_closes_nothing(self):
        from engineering import fact_closure as F
        self.assertTrue(F.verify_recorded_facts({}, self.live()))
        self.assertTrue(F.verify_recorded_facts(None, self.live()))

    def test_the_recorded_set_must_agree_with_what_the_code_declares(self):
        from engineering import fact_closure as F
        problems = F.verify_recorded_facts(self.recorded("a_mm"), self.live(), declared_ids=("a_mm", "b_mm"))
        self.assertTrue(problems and "b_mm" in problems[0])
        problems = F.verify_recorded_facts(self.recorded("a_mm", "b_mm"), self.live(), declared_ids=("a_mm",))
        self.assertTrue(problems and "b_mm" in problems[0])
        self.assertEqual(F.verify_recorded_facts(self.recorded("a_mm", "b_mm"), self.live(),
                                                 declared_ids=("b_mm", "a_mm")), [])

    def test_a_field_the_producer_never_wrote_down_cannot_be_compared(self):
        from engineering import fact_closure as F
        partial = self.recorded("a_mm", omit=("basis", "revision"))
        problems = F.verify_recorded_facts(partial, self.live())
        self.assertTrue(problems and "basis" in problems[0] and "revision" in problems[0])
        self.assertEqual(F.verify_recorded_facts(partial, self.live(),
                                                 unrecorded_ok=("basis", "revision")), [])

    def test_a_record_without_what_changes_a_computation_is_refused(self):
        from engineering import fact_closure as F
        for omitted in ("value", "unit", "provenance"):
            with self.subTest(field=omitted):
                broken = self.recorded("a_mm", omit=(omitted,))
                problems = F.verify_recorded_facts(broken, self.live(), unrecorded_ok=("basis", "revision"))
                self.assertTrue(problems and omitted in problems[0])

    def test_a_fact_that_left_the_manifest_is_a_problem(self):
        from engineering import fact_closure as F
        problems = F.verify_recorded_facts(self.recorded("a_mm"), self.live(a_mm=None))
        self.assertTrue(problems and "a_mm" in problems[0])


class ResolvedRecordsAreComparedThroughTheResolver(unittest.TestCase):
    """Most evidence in the tree wrote down what ``FactManifest.resolve`` returned,
    not the raw manifest entry. That shape has already collapsed two pairs of
    alternatives: ``scope`` falls back to ``revision`` and ``datum`` to ``basis``.
    Comparing such a record against the raw entry would report a move that never
    happened, so it is compared through the same resolver that produced it.

    ``status`` and ``detail`` are deliberately not compared: they answer what the
    consumer demanded of the fact (unit, provenance, scope), not what the manifest
    says. A fact that lost its value still fails here, because the resolver then
    reports no value at all.
    """

    LIVE = {"parameters": {
        "a_mm": {"value": 1.0, "unit": "mm", "provenance": "MEASURED", "source": "s",
                 "datum": "front face", "scope": "unit-1"},
        "old_mm": {"value": 3.0, "unit": "mm", "provenance": "MEASURED", "source": "s",
                   "basis": "rear face", "revision": "unit-0"},
        "spare_mm": {"value": 9.0, "unit": "mm", "provenance": "UNKNOWN", "source": "s"},
    }}

    def live(self, **changes):
        import copy
        data = copy.deepcopy(self.LIVE)
        for fact_id, fields in changes.items():
            if fields is None:
                data["parameters"].pop(fact_id, None)
            else:
                data["parameters"].setdefault(fact_id, {}).update(fields)
        return json.dumps(data).encode()

    def recorded(self, *ids, omit=(), **overrides):
        """Exactly what a producer writes down: ``FactResolution.as_dict()``."""
        from engineering.facts import FactManifest
        facts = FactManifest(json.loads(self.live().decode()))
        out = {}
        for fact_id in ids:
            row = facts.resolve(fact_id).as_dict()
            out[fact_id] = {k: v for k, v in row.items() if k not in omit}
        for fact_id, fields in overrides.items():
            out[fact_id].update(fields)
        return out

    def test_the_compared_fields_are_the_ones_the_resolver_exposes(self):
        from engineering.facts import FactResolution, FactStatus
        exposed = set(FactResolution("x", FactStatus.READY).as_dict())
        self.assertEqual(exposed - {"fact_id", "status", "detail"}, set(FC.RESOLVED_FIELDS))

    def test_unchanged_facts_report_nothing(self):
        self.assertEqual([], FC.verify_resolved_facts(self.recorded("a_mm", "old_mm"), self.live()))

    def test_a_record_written_from_revision_and_basis_is_not_a_false_move(self):
        """The producer recorded scope "unit-0" and datum "rear face"; the manifest
        spells them ``revision`` and ``basis``. Nothing moved."""
        record = self.recorded("old_mm")
        self.assertEqual(record["old_mm"]["scope"], "unit-0")
        self.assertEqual(record["old_mm"]["datum"], "rear face")
        self.assertEqual([], FC.verify_resolved_facts(record, self.live()))

    def test_moving_the_alternative_spelling_is_still_a_move(self):
        for field, value in (("revision", "unit-9"), ("basis", "top face")):
            with self.subTest(field=field):
                problems = FC.verify_resolved_facts(self.recorded("old_mm"),
                                                    self.live(old_mm={field: value}))
                self.assertTrue(problems and "old_mm" in problems[0])

    def test_a_moved_value_is_named_with_its_field(self):
        problems = FC.verify_resolved_facts(self.recorded("a_mm"), self.live(a_mm={"value": 2.0}))
        self.assertEqual(1, len(problems))
        self.assertIn("a_mm", problems[0])
        self.assertIn("value", problems[0])

    def test_every_resolved_field_is_compared(self):
        for field, value in (("value", 2.0), ("unit", "cm"), ("provenance", "CALCULATED"),
                             ("source", "other"), ("scope", "unit-2"), ("datum", "rear face")):
            with self.subTest(field=field):
                problems = FC.verify_resolved_facts(self.recorded("a_mm"), self.live(a_mm={field: value}))
                self.assertTrue(problems and field in problems[0])

    def test_an_unrelated_fact_is_not_this_artifact_s_business(self):
        for change in ({"spare_mm": {"value": 42.0}}, {"new_fact_mm": {"value": 1.0, "unit": "mm"}}):
            with self.subTest(change=sorted(change)):
                self.assertEqual([], FC.verify_resolved_facts(self.recorded("a_mm"), self.live(**change)))

    def test_what_the_consumer_demanded_is_not_compared(self):
        """status and detail record the caller's expectations, not the manifest."""
        record = self.recorded("a_mm", a_mm={"status": "WRONG_UNIT", "detail": "expected 'cm'"})
        self.assertEqual([], FC.verify_resolved_facts(record, self.live()))

    def test_a_fact_that_lost_its_value_is_refused(self):
        problems = FC.verify_resolved_facts(self.recorded("a_mm"), self.live(a_mm={"value": None}))
        self.assertTrue(problems and "a_mm" in problems[0])

    def test_a_fact_that_left_the_manifest_is_refused(self):
        problems = FC.verify_resolved_facts(self.recorded("a_mm"), self.live(a_mm=None))
        self.assertTrue(problems and "a_mm" in problems[0] and "manifest" in problems[0])

    def test_an_empty_record_set_closes_nothing(self):
        self.assertTrue(FC.verify_resolved_facts({}, self.live()))
        self.assertTrue(FC.verify_resolved_facts(None, self.live()))

    def test_the_recorded_set_must_agree_with_what_the_code_declares(self):
        problems = FC.verify_resolved_facts(self.recorded("a_mm"), self.live(),
                                            declared_ids=("a_mm", "old_mm"))
        self.assertTrue(problems and "old_mm" in problems[0])
        problems = FC.verify_resolved_facts(self.recorded("a_mm", "old_mm"), self.live(),
                                            declared_ids=("a_mm",))
        self.assertTrue(problems and "old_mm" in problems[0])
        self.assertEqual([], FC.verify_resolved_facts(self.recorded("a_mm", "old_mm"), self.live(),
                                                      declared_ids=("old_mm", "a_mm")))

    def test_a_field_the_producer_never_wrote_down_cannot_be_compared(self):
        for field in FC.RESOLVED_FIELDS:
            with self.subTest(field=field):
                problems = FC.verify_resolved_facts(self.recorded("a_mm", omit=(field,)), self.live())
                self.assertTrue(problems and field in problems[0])

    def test_an_unreadable_manifest_is_a_problem_not_a_pass(self):
        self.assertTrue(FC.verify_resolved_facts(self.recorded("a_mm"), b"not json"))
