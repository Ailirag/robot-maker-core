"""Bounded maintenance of the disposable artifact cache.

The sweep lives outside the cached procedure on purpose: ``artifact_cache``
mixes the bytes of its own module into every key, so maintenance written
there would invalidate the cache it maintains (CORE_CORRECTION_FINDINGS
K26). Here: nothing is removed without a stated budget, only real entries
are ever candidates, and a sweep is always safe for validity because a
removed entry is simply recomputed.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engineering import artifact_cache as AC  # noqa: E402
from engineering import cache_gc  # noqa: E402

DAY = 86400.0


def _entry(directory: Path, kind: str, inputs, payload: bytes, *, age_days: float = 0.0) -> str:
    """One real cache entry, written by the cache itself, aged by its mtime."""
    cache = AC.Cache(directory)
    key = cache.key(kind, inputs)
    cache.get(kind, inputs, lambda: payload)
    path = directory / (key + ".json")
    if age_days:
        stamp = time.time() - age_days * DAY
        os.utime(path, (stamp, stamp))
    return key


class OnlyRealEntriesAreCandidates(unittest.TestCase):
    def test_foreign_and_temporary_files_are_reported_never_removed(self):
        with _tmp() as directory:
            key = _entry(directory, "k", {"a": 1}, b"payload", age_days=99)
            keep_me = directory / "NOTES.md"
            keep_me.write_text("not a cache entry\n", encoding="utf-8")
            leftover = directory / "entry-abcdef"
            leftover.write_bytes(b"interrupted write")
            nested = directory / "sub"
            nested.mkdir()
            (nested / "deadbeef.json").write_text("{}", encoding="utf-8")

            report = cache_gc.sweep(directory, max_age_seconds=DAY, apply=True)

            self.assertNotIn(key, {p.name[:-5] for p in directory.glob("*.json")})
            self.assertTrue(keep_me.is_file())
            self.assertTrue(leftover.is_file())
            self.assertTrue((nested / "deadbeef.json").is_file())
            self.assertEqual(report["foreign"]["count"], 1)
            self.assertEqual(report["temporary"]["count"], 1)

    def test_a_symlinked_entry_is_never_followed_or_removed(self):
        with _tmp() as directory:
            target = directory / "payload.bin"
            target.write_bytes(b"x" * 16)
            link = directory / ("f" * 64 + ".json")
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are not available for this user")
            report = cache_gc.sweep(directory, max_age_seconds=0.0, apply=True)
            self.assertTrue(link.is_symlink())
            self.assertEqual(report["removed"], [])

    def test_an_entry_reported_as_a_link_is_excluded_on_every_platform(self):
        """The same branch without needing the privilege to create a symlink."""
        with _tmp() as directory:
            key = _entry(directory, "k", {"a": 1}, b"payload", age_days=99)
            path = directory / (key + ".json")
            real = Path.is_symlink
            with mock.patch.object(Path, "is_symlink",
                                   lambda self: self == path or real(self)):
                report = cache_gc.sweep(directory, max_age_seconds=DAY, apply=True)
            self.assertTrue(path.is_file())
            self.assertEqual(report["removed"], [])
            self.assertEqual(report["before"]["entries"], 0)


class NothingGoesWithoutABudget(unittest.TestCase):
    def test_a_report_without_apply_removes_nothing(self):
        with _tmp() as directory:
            key = _entry(directory, "k", {"a": 1}, b"payload", age_days=99)
            report = cache_gc.sweep(directory, max_age_seconds=DAY)
            self.assertTrue((directory / (key + ".json")).is_file())
            self.assertEqual(report["applied"], False)
            self.assertEqual([r["key"] for r in report["removed"]], [key])

    def test_apply_without_a_budget_keeps_every_readable_entry(self):
        with _tmp() as directory:
            old = _entry(directory, "k", {"a": 1}, b"payload", age_days=365)
            new = _entry(directory, "k", {"a": 2}, b"payload2")
            report = cache_gc.sweep(directory, apply=True)
            self.assertTrue((directory / (old + ".json")).is_file())
            self.assertTrue((directory / (new + ".json")).is_file())
            self.assertEqual(report["removed"], [])


class DeadEntriesAreSweptWhateverTheBudget(unittest.TestCase):
    def test_a_corrupt_entry_can_never_hit_and_is_removed_by_apply(self):
        with _tmp() as directory:
            good = _entry(directory, "k", {"a": 1}, b"payload")
            broken = directory / ("a" * 64 + ".json")
            broken.write_text("{not json", encoding="utf-8")
            mismatched = directory / ("b" * 64 + ".json")
            mismatched.write_text(json.dumps(
                {"key": "c" * 64, "sha256": "0" * 64, "data": ""}), encoding="utf-8")

            report = cache_gc.sweep(directory, apply=True)

            self.assertFalse(broken.exists())
            self.assertFalse(mismatched.exists())
            self.assertTrue((directory / (good + ".json")).is_file())
            self.assertEqual({r["reason"] for r in report["removed"]}, {"corrupt"})


class AgeAndSizeBudgets(unittest.TestCase):
    def test_age_removes_only_what_is_older_than_the_budget(self):
        with _tmp() as directory:
            old = _entry(directory, "k", {"a": 1}, b"old", age_days=10)
            fresh = _entry(directory, "k", {"a": 2}, b"fresh", age_days=1)
            report = cache_gc.sweep(directory, max_age_seconds=5 * DAY, apply=True)
            self.assertFalse((directory / (old + ".json")).exists())
            self.assertTrue((directory / (fresh + ".json")).is_file())
            self.assertEqual([(r["key"], r["reason"]) for r in report["removed"]],
                             [(old, "age")])

    def test_size_removes_the_oldest_first_until_under_budget(self):
        with _tmp() as directory:
            oldest = _entry(directory, "k", {"a": 1}, b"z" * 4096, age_days=3)
            middle = _entry(directory, "k", {"a": 2}, b"z" * 4096, age_days=2)
            newest = _entry(directory, "k", {"a": 3}, b"z" * 4096, age_days=1)
            total = sum(p.stat().st_size for p in directory.glob("*.json"))

            report = cache_gc.sweep(directory, max_bytes=total // 2, apply=True)

            self.assertFalse((directory / (oldest + ".json")).exists())
            self.assertTrue((directory / (newest + ".json")).is_file())
            self.assertLessEqual(report["after"]["bytes"], total // 2)
            self.assertEqual(report["removed"][0]["key"], oldest)
            self.assertEqual({r["reason"] for r in report["removed"]}, {"size"})
            self.assertIn(middle, {oldest, middle, newest})

    def test_kept_keys_survive_both_budgets_and_are_reported_over_budget(self):
        with _tmp() as directory:
            pinned = _entry(directory, "k", {"a": 1}, b"z" * 4096, age_days=99)
            report = cache_gc.sweep(directory, max_bytes=1, max_age_seconds=DAY,
                                    keep=[pinned], apply=True)
            self.assertTrue((directory / (pinned + ".json")).is_file())
            self.assertEqual(report["removed"], [])
            self.assertTrue(report["over_budget"])

    def test_a_missing_directory_is_an_empty_report_not_an_error(self):
        with _tmp() as directory:
            report = cache_gc.sweep(directory / "absent", apply=True)
            self.assertEqual(report["before"]["entries"], 0)
            self.assertEqual(report["removed"], [])


class BudgetFromEnvironment(unittest.TestCase):
    def test_absent_variables_mean_no_budget(self):
        self.assertEqual(cache_gc.budget_from_env({}), (None, None))

    def test_values_are_read_as_bytes_and_seconds(self):
        self.assertEqual(
            cache_gc.budget_from_env({"ENGINEERING_CACHE_MAX_BYTES": "1024",
                                      "ENGINEERING_CACHE_MAX_AGE_DAYS": "1.5"}),
            (1024, 1.5 * DAY))

    def test_an_unusable_value_refuses_instead_of_sweeping_by_accident(self):
        for env in ({"ENGINEERING_CACHE_MAX_BYTES": "lots"},
                    {"ENGINEERING_CACHE_MAX_AGE_DAYS": "-1"},
                    {"ENGINEERING_CACHE_MAX_BYTES": "-8"}):
            with self.subTest(env=env):
                with self.assertRaises(ValueError):
                    cache_gc.budget_from_env(env)


class CommandLine(unittest.TestCase):
    def test_default_run_reports_json_and_writes_nothing(self):
        with _tmp() as directory:
            key = _entry(directory, "k", {"a": 1}, b"payload", age_days=99)
            out = _capture(["--directory", str(directory), "--max-age-days", "1"])
            self.assertEqual(out["code"], 0)
            self.assertFalse(out["report"]["applied"])
            self.assertTrue((directory / (key + ".json")).is_file())

    def test_apply_removes_and_reports_the_same_entries(self):
        with _tmp() as directory:
            key = _entry(directory, "k", {"a": 1}, b"payload", age_days=99)
            out = _capture(["--directory", str(directory), "--max-age-days", "1", "--apply"])
            self.assertEqual(out["code"], 0)
            self.assertTrue(out["report"]["applied"])
            self.assertEqual([r["key"] for r in out["report"]["removed"]], [key])
            self.assertFalse((directory / (key + ".json")).exists())


class TheSweepIsNotAnApproval(unittest.TestCase):
    def test_the_module_states_that_a_removed_entry_is_only_recomputed(self):
        text = (ROOT / "engineering" / "cache_gc.py").read_text(encoding="utf-8")
        self.assertIn("recompute", text.lower())
        self.assertNotIn("import engineering.instructions", text)


# --------------------------------------------------------------------------- helpers
import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402


@contextlib.contextmanager
def _tmp():
    with tempfile.TemporaryDirectory(prefix="cache-gc-test-") as name:
        yield Path(name)


def _capture(argv):
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        code = cache_gc.main(argv)
    return {"code": code, "report": json.loads(stream.getvalue())}


if __name__ == "__main__":
    unittest.main()
