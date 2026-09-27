"""Read exact index blobs into a disposable tree, then check in a fresh Python.

No checkout filters, archive attributes, worktree files, index writes or CAD
generation participate. Unsupported links and unresolved stages fail closed.
Blobs are streamed through one cat-file process, including large artifacts.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


def index_listing(root: Path) -> bytes:
    return subprocess.run(["git", "-C", str(root), "ls-files", "--stage", "-z"],
                          check=True, capture_output=True).stdout


def export_index(root: Path, destination: Path, listing: bytes) -> None:
    """Export only regular stage-zero blobs, with no path escaping the new tree."""
    entries = []
    for row in listing.split(b"\0"):
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        rel = PurePosixPath(name.decode("utf-8"))
        if (stage != b"0" or mode not in (b"100644", b"100755")
                or rel.is_absolute() or ".." in rel.parts or "\\" in str(rel)
                or ":" in str(rel) or any(p.lower() == ".git" for p in rel.parts)):
            raise ValueError(f"unsupported index entry: {rel} (mode={mode!r}, stage={stage!r})")
        target = destination.joinpath(*rel.parts)
        if not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError(f"index path escapes snapshot: {rel}")
        entries.append((oid, target))

    # File stdin avoids a pipe deadlock when the index lists many large blobs.
    with tempfile.TemporaryFile() as requests, tempfile.TemporaryFile() as errors:
        for oid, _ in entries:
            requests.write(oid + b"\n")
        requests.seek(0)
        proc = subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"],
                                stdin=requests, stdout=subprocess.PIPE, stderr=errors)
        try:
            assert proc.stdout is not None
            for oid, target in entries:
                header = proc.stdout.readline().split()
                if len(header) != 3 or header[:2] != [oid, b"blob"]:
                    raise ValueError(f"cannot read index blob for {target.name}: {header!r}")
                remaining = int(header[2])
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    while remaining:
                        chunk = proc.stdout.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError(f"truncated index blob: {target.name}")
                        output.write(chunk)
                        remaining -= len(chunk)
                if proc.stdout.read(1) != b"\n":
                    raise ValueError("invalid cat-file separator")
            if proc.wait() != 0:
                errors.seek(0)
                raise ValueError(errors.read().decode("utf-8", errors="replace"))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()


CHECK = """import contextlib, dataclasses, json, sys
sys.path.insert(0, sys.argv[1])
def emit(value):
    print(json.dumps(value), file=sys.__stdout__, flush=True)
emit({"phase": "load registry"})
with contextlib.redirect_stdout(sys.stderr):
    from engineering.project_contract import project_module
    from engineering.instructions.check import problems
    data = project_module('views')
    if callable(getattr(data, 'views', None)):
        entries = data.views()
        def measured(view):
            def fresh():
                emit({"phase": view.label})
                return view.fresh()
            return dataclasses.replace(view, fresh=fresh)
        wrapped = [measured(view) for view in entries]
        data.views = lambda: wrapped
    result = data.stale()
    emit({"phase": "instruction prerequisites"})
    result += problems()
emit({"result": result})
"""


def _run_check(snapshot, env, *, phase_timeout=300, total_timeout=900):
    """One isolated checker; every phase and the entire run have hard bounds."""
    started = time.monotonic()
    phase = "checker startup"
    result = None
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", CHECK, str(snapshot)],
            cwd=snapshot, env=env, stdout=subprocess.PIPE, stderr=errors,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        events = queue.Queue()
        def read_events():
            try:
                for line in proc.stdout:
                    events.put(line)
            finally:
                events.put(None)
        reader = threading.Thread(target=read_events, daemon=True)
        reader.start()
        try:
            while True:
                remaining = total_timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError(f"staged check total budget exceeded ({total_timeout}s)")
                try:
                    line = events.get(timeout=min(phase_timeout, remaining))
                except queue.Empty:
                    if time.monotonic() - started >= total_timeout:
                        raise TimeoutError(f"staged check total budget exceeded ({total_timeout}s)") from None
                    raise TimeoutError(f"staged check phase exceeded {phase_timeout}s: {phase}") from None
                if line is None:
                    break
                item = json.loads(line)
                if not isinstance(item, dict) or result is not None:
                    raise ValueError("staged check returned an invalid event sequence")
                if set(item) == {"phase"} and isinstance(item["phase"], str):
                    phase = item["phase"]
                    print(f"freshness: {phase[:160]}", flush=True)
                elif (set(item) == {"result"} and isinstance(item["result"], list)
                      and all(isinstance(p, str) for p in item["result"])):
                    result = item["result"]
                else:
                    raise ValueError("staged check returned a malformed event")
            remaining = total_timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("staged check total budget exceeded")
            code = proc.wait(timeout=min(phase_timeout, remaining))
            if code:
                errors.seek(0, 2)
                errors.seek(max(0, errors.tell() - 2000))
                raise ValueError(f"staged check exited {code}: {errors.read().decode('utf-8', errors='replace')}")
            if result is None:
                raise ValueError("staged check returned no result")
            print(f"freshness complete: {time.monotonic()-started:.1f}s", flush=True)
            return result
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            reader.join(timeout=1)
            proc.stdout.close()


def freshness(root: Path, listing: bytes) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="engineering-index-") as temporary:
        snapshot = Path(temporary)
        export_index(root, snapshot, listing)
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        return _run_check(snapshot, env)
