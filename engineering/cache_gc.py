"""Bounded maintenance of the disposable artifact cache; never an acceptance record.

    python -m engineering.cache_gc                       # inventory, writes nothing
    python -m engineering.cache_gc --max-age-days 14 --apply
    python -m engineering.cache_gc --max-bytes 2000000000 --apply

This lives outside the cached procedure deliberately. ``artifact_cache.Cache.key``
mixes the bytes of its own module into every key, so a sweep written inside
``artifact_cache.py`` would invalidate the whole cache it was meant to bound, and
the FreeCAD stages that pin that module as a producer would all go stale at the
same time (``engineering/CORE_CORRECTION_FINDINGS.md``, K25 and K26). Nothing here
is imported by a stage, so nothing here can make one stale.

Removing an entry can never change a result: the next request for it is a miss and
the producer recomputes the same bytes. The sweep therefore costs time, never
validity - and it grants nothing. Freshness, gates, holds and physical acceptance
keep their own verdicts.

Fail-closed by default: a plain run reports and writes nothing, a run without a
stated budget removes only entries that can never be read back, and an unusable
budget refuses instead of guessing one.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

from engineering import artifact_cache as AC

#: An entry file written by ``artifact_cache``: its key, then ``.json``.
ENTRY = re.compile(r"^[0-9a-f]{64}\.json$")
#: Prefix of the atomic-write temporaries; a leftover means an interrupted writer.
TEMPORARY = "entry-"
DAY = 86400.0


def _entries(directory: Path) -> list[tuple[str, Path, os.stat_result]]:
    """Readable, regular entry files only, oldest first. A symlink is never an
    entry: the cache never writes one and following one would leave the directory."""
    out = []
    for path in sorted(directory.iterdir()):
        if not ENTRY.match(path.name) or path.is_symlink() or not path.is_file():
            continue
        out.append((path.name[:-5], path, path.stat()))
    out.sort(key=lambda item: (item[2].st_mtime, item[0]))
    return out


def _readable(directory: Path, key: str) -> bool:
    """Would the cache find this entry usable? ``Cache._read`` is the one answer;
    a public wrapper cannot be added to that module without changing every key."""
    return AC.Cache(directory)._read(key) is not None


def _other_files(directory: Path) -> tuple[dict, dict]:
    """What the sweep will not touch: interrupted writes, and everything else."""
    temporary = {"count": 0, "bytes": 0}
    foreign = {"count": 0, "bytes": 0}
    for path in sorted(directory.iterdir()):
        if path.is_dir() or ENTRY.match(path.name):
            continue
        bucket = temporary if path.name.startswith(TEMPORARY) else foreign
        bucket["count"] += 1
        try:
            bucket["bytes"] += path.stat().st_size
        except OSError:
            pass
    return temporary, foreign


def budget_from_env(env=None) -> tuple[int | None, float | None]:
    """``ENGINEERING_CACHE_MAX_BYTES`` and ``ENGINEERING_CACHE_MAX_AGE_DAYS``.
    Absent means no budget; present and unusable refuses."""
    env = os.environ if env is None else env
    raw_bytes = env.get("ENGINEERING_CACHE_MAX_BYTES")
    raw_days = env.get("ENGINEERING_CACHE_MAX_AGE_DAYS")
    max_bytes = None
    max_age = None
    if raw_bytes not in (None, ""):
        try:
            max_bytes = int(raw_bytes)
        except (TypeError, ValueError):
            raise ValueError(f"CACHE_GC: ENGINEERING_CACHE_MAX_BYTES is not an integer: {raw_bytes!r}")
        if max_bytes < 0:
            raise ValueError("CACHE_GC: ENGINEERING_CACHE_MAX_BYTES must not be negative")
    if raw_days not in (None, ""):
        try:
            days = float(raw_days)
        except (TypeError, ValueError):
            raise ValueError(f"CACHE_GC: ENGINEERING_CACHE_MAX_AGE_DAYS is not a number: {raw_days!r}")
        if days < 0:
            raise ValueError("CACHE_GC: ENGINEERING_CACHE_MAX_AGE_DAYS must not be negative")
        max_age = days * DAY
    return max_bytes, max_age


def sweep(directory, *, max_bytes: int | None = None, max_age_seconds: float | None = None,
          keep=(), apply: bool = False, now: float | None = None) -> dict:
    """Plan (and with ``apply``, perform) the removals that bring the cache
    directory inside its budget. Returns the plan either way."""
    directory = Path(directory)
    kept = frozenset(keep)
    now = time.time() if now is None else now
    if not directory.is_dir():
        empty = {"count": 0, "bytes": 0}
        return {"directory": directory.as_posix(), "applied": bool(apply),
                "before": {"entries": 0, "bytes": 0}, "after": {"entries": 0, "bytes": 0},
                "removed": [], "kept": sorted(kept), "over_budget": False,
                "temporary": dict(empty), "foreign": dict(empty)}

    entries = _entries(directory)
    temporary, foreign = _other_files(directory)
    before = {"entries": len(entries), "bytes": sum(s.st_size for _, _, s in entries)}

    planned: list[dict] = []
    surviving: list[tuple[str, Path, os.stat_result]] = []
    for key, path, stat in entries:
        if key in kept:
            surviving.append((key, path, stat))
            continue
        # A dead entry is never a hit, so its bytes are pure cost whatever the budget.
        if not _readable(directory, key):
            planned.append({"key": key, "reason": "corrupt", "bytes": stat.st_size})
            continue
        if max_age_seconds is not None and (now - stat.st_mtime) > max_age_seconds:
            planned.append({"key": key, "reason": "age", "bytes": stat.st_size})
            continue
        surviving.append((key, path, stat))

    if max_bytes is not None:
        remaining = sum(s.st_size for _, _, s in surviving)
        for key, _, stat in list(surviving):  # oldest first
            if remaining <= max_bytes:
                break
            if key in kept:
                continue
            planned.append({"key": key, "reason": "size", "bytes": stat.st_size})
            surviving = [item for item in surviving if item[0] != key]
            remaining -= stat.st_size

    if apply:
        for record in planned:
            try:
                (directory / (record["key"] + ".json")).unlink()
            except OSError as exc:
                record["error"] = str(exc)

    after_entries = _entries(directory) if apply else surviving
    after = {"entries": len(after_entries), "bytes": sum(s.st_size for _, _, s in after_entries)}
    over = bool(max_bytes is not None and after["bytes"] > max_bytes)
    return {"directory": directory.as_posix(), "applied": bool(apply),
            "before": before, "after": after, "removed": planned,
            "kept": sorted(kept), "over_budget": over,
            "temporary": temporary, "foreign": foreign}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=None,
                        help="cache directory; default is the one artifact_cache uses")
    parser.add_argument("--max-bytes", type=int, default=None)
    parser.add_argument("--max-age-days", type=float, default=None)
    parser.add_argument("--apply", action="store_true",
                        help="remove the planned entries; without it nothing is written")
    args = parser.parse_args(argv)
    try:
        env_bytes, env_age = budget_from_env()
        max_bytes = args.max_bytes if args.max_bytes is not None else env_bytes
        max_age = args.max_age_days * DAY if args.max_age_days is not None else env_age
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("CACHE_GC: --max-bytes must not be negative")
        if max_age is not None and max_age < 0:
            raise ValueError("CACHE_GC: --max-age-days must not be negative")
        directory = args.directory if args.directory is not None else AC.default_cache().directory
        report = sweep(directory, max_bytes=max_bytes, max_age_seconds=max_age, apply=args.apply)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
