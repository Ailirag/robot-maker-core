"""Freshness of generated documents: the mechanism (process core).

A generated view is a label, the command a human runs to regenerate it, a
``fresh()`` that renders every path it owns in memory, and a ``write()``.
The list of views is project data (``engineering/generated_views.py``);
this module only compares committed bytes with a fresh render and writes.

Freshness compares the exact bytes declared by each view's LF/CRLF/binary
contract. Dependencies are ordered before writing or comparing outputs.
Nothing here is a source of truth: delete every generated file and nothing
is lost.
"""
from __future__ import annotations

import importlib.util
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class View:
    label: str
    regen: str
    fresh: Callable[[], dict[str, str | bytes]]
    write: Callable[[], None]
    dependencies: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    runtime: str = 'python>=3.11'
    eol: str = 'LF'


def ordered(views: Iterable[View]) -> list[View]:
    """Stable topological order, with no implicit missing generator."""
    entries = list(views)
    by_label = {view.label: view for view in entries}
    if len(entries) != len(by_label):
        raise ValueError('DUPLICATE_VIEW: labels must be unique')
    for view in entries:
        if view.runtime != 'python>=3.11' or view.eol not in {'LF', 'CRLF', 'binary'}:
            raise ValueError(f'VIEW_CONTRACT: unsupported runtime/EOL for {view.label}')
        for dependency in view.dependencies:
            if dependency not in by_label:
                raise ValueError(f'MISSING_DEPENDENCY: {view.label} requires {dependency}')
        for source in view.sources:
            path = (REPO / source).resolve()
            if Path(source).is_absolute() or not path.is_relative_to(REPO.resolve()) or not path.is_file():
                raise ValueError(f'MISSING_SOURCE: {view.label}: {source}')
    done = set()
    out = []
    while len(done) < len(entries):
        ready = [view for view in entries if view.label not in done and set(view.dependencies) <= done]
        if not ready:
            raise ValueError('DEPENDENCY_CYCLE: ' + ', '.join(v.label for v in entries if v.label not in done))
        for view in ready:
            out.append(view)
            done.add(view.label)
    return out


def encoded(view: View, content: str | bytes) -> bytes:
    if isinstance(content, bytes):
        return content
    if view.eol == 'binary':
        raise ValueError(f'VIEW_CONTRACT: {view.label} must render bytes')
    text = normalised(content)
    if view.eol == 'CRLF':
        text = text.replace('\n', '\r\n')
    return text.encode('utf-8')


def path_in(folder: Path) -> None:
    """Make a script folder importable (project renderers live next to their data)."""
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))


def load(name: str, path: Path):
    """Import a script by path once, under a stable module name."""
    path = Path(path).resolve()
    if name in sys.modules:
        module = sys.modules[name]
        origin = getattr(module, '__file__', None)
        if not origin or Path(origin).resolve() != path:
            raise ValueError(f'IMPORT_ORIGIN: {name} is already bound to {origin}, expected {path}')
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def write_lf(rel: str, text: str) -> None:
    (REPO / rel).write_text(text, encoding="utf-8", newline="\n")


def normalised(text: str) -> str:
    return text.replace("\r\n", "\n")


def stale(views: Iterable[View]) -> list[str]:
    """Every generated path whose committed bytes differ from a fresh render."""
    problems = []
    for view in ordered(views):
        for rel, content in view.fresh().items():
            target = REPO / rel
            if not target.is_file():
                problems.append(f"STALE: {rel} is missing; run {view.regen}")
                continue
            same = target.read_bytes() == encoded(view, content)
            if not same:
                problems.append(f"STALE: {rel} differs from a fresh render; run {view.regen}")
    return problems


def write_all(views: Iterable[View]) -> list[str]:
    written = []
    expected = {}
    for view in ordered(views):
        # Render once and only replace changed declared outputs. Unchanged files
        # retain their timestamps; downstream views see new dependency bytes.
        for rel, content in view.fresh().items():
            target = (REPO / rel).resolve()
            if Path(rel).is_absolute() or not target.is_relative_to(REPO.resolve()):
                raise ValueError(f'VIEW_OUTPUT: path outside repository: {rel}')
            raw = encoded(view, content)
            expected[target] = hashlib.sha256(raw).hexdigest()
            if target.is_file() and target.read_bytes() == raw:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            written.append(rel)
    # Verify the produced bytes without executing every generator again.
    # Independent --check/pre-commit still perform a fresh render.
    for target, digest in expected.items():
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"OUTPUT_CHANGED: {target}")
    return written
