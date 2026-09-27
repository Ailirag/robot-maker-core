"""Observe repository reads in a fresh, single-threaded producer process.

Python file reads and FactManifest calls are observed separately. Native
libraries' file reads must be declared through explicit inputs by the producer.
Runtime resources outside the repository belong to the runtime lock, not this
file closure. This is dependency observation, not a security sandbox.
"""
from __future__ import annotations

import builtins
from contextlib import contextmanager
from copy import deepcopy
import inspect
import io
from pathlib import Path
import threading

from engineering import build_composition as C, fact_closure
from engineering.facts import FactManifest

_LOCK = threading.Lock()


class Observation:
    def __init__(self, root: Path, *, explicit=()):
        self.root = Path(root).resolve()
        self.explicit = tuple(explicit)
        self.files = []
        self.facts = []
        self._file_pins = {}
        self._fact_pins = {}
        self._instances = {}
        self._depth = 0

    @contextmanager
    def _suspend(self):
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1

    def _file(self, path):
        path = Path(path).absolute()
        if not path.is_relative_to(self.root):
            return
        rel = path.relative_to(self.root).as_posix()
        with self._suspend():
            pin = C.file_pin(self.root, rel)
        previous = self._file_pins.setdefault(rel, pin)
        if previous != pin:
            raise ValueError(f'INPUT_CHANGED during producer: {rel}')

    def _open(self, original, file, mode='r', *args, **kwargs):
        if not self._depth and isinstance(file, (str, Path)) and not any(c in mode for c in 'wax+'):
            self._file(file)
        return original(file, mode, *args, **kwargs)

    def _from_path(self, cls, path):
        rel = Path(path).absolute().relative_to(self.root).as_posix()
        with self._suspend():
            C._path(self.root, rel)
            instance = self._original_from_path.__func__(cls, path)
        self._instances[id(instance)] = (instance, rel)
        return instance

    def _recipe(self, instance):
        if id(instance) not in self._instances:
            raise ValueError('FACT_ORIGIN: unobserved manifest; use a fresh isolated producer')
        rel = self._instances[id(instance)][1]
        recipe = next((f for f in self.facts if f['path'] == rel), None)
        if recipe is None:
            recipe = {'path': rel, 'contracts': {}, 'flags': []}
            self.facts.append(recipe)
        return recipe

    def _resolve(self, instance, fact_id, **kwargs):
        if self._depth:
            return self._original_resolve(instance, fact_id, **kwargs)
        recipe = self._recipe(instance)
        bound = inspect.signature(self._original_resolve).bind(instance, fact_id, **kwargs)
        bound.apply_defaults()
        contract = {k: bound.arguments[k] for k in C.FACT_CONTRACT_FIELDS}
        contract['allowed_provenance'] = sorted(contract['allowed_provenance'] or [])
        old = recipe['contracts'].get(fact_id)
        if old is not None:
            if old['expected_unit'] != contract['expected_unit']:
                raise ValueError(f'FACT_CONTRACT: inconsistent units for {fact_id}')
            a, b = old['expected_scope'], contract['expected_scope']
            if a is not None and b is not None and a != b:
                raise ValueError(f'FACT_CONTRACT: inconsistent scope for {fact_id}')
            contract.update(expected_scope=a if a is not None else b,
                require_datum=old['require_datum'] or contract['require_datum'],
                allowed_provenance=sorted(set(old['allowed_provenance']) & set(contract['allowed_provenance'])))
        recipe['contracts'][fact_id] = contract
        with self._suspend():
            snapshot = C.capture_facts(self.root, recipe)
            actual = fact_closure.fact_record(instance._parameters, fact_id)
            if actual != snapshot['records'][fact_id]:
                raise ValueError(f'FACT_CHANGED during producer: {fact_id}')
        self._remember_facts(recipe['path'], snapshot)
        return self._original_resolve(instance, fact_id, **kwargs)

    def _flag(self, instance, flag_id):
        result = self._original_flag(instance, flag_id)
        if self._depth:
            return result
        recipe = self._recipe(instance)
        recipe['flags'] = sorted(set(recipe['flags']) | {flag_id})
        with self._suspend():
            snapshot = C.capture_facts(self.root, recipe)
        if result != snapshot['flags'][flag_id]:
            raise ValueError(f'FACT_CHANGED during producer: {flag_id}')
        self._remember_facts(recipe['path'], snapshot)
        return result

    def _remember_facts(self, path, snapshot):
        previous = self._fact_pins.get(path, {})
        for section in ('records', 'flags'):
            for key, record in previous.get(section, {}).items():
                if snapshot[section].get(key) != record:
                    raise ValueError(f'FACT_CHANGED during producer: {key}')
        self._fact_pins[path] = deepcopy(snapshot)

    def verify_unchanged(self):
        with self._suspend():
            for rel, pin in self._file_pins.items():
                if C.file_pin(self.root, rel) != pin:
                    raise ValueError(f'INPUT_CHANGED during producer: {rel}')
            for recipe in self.facts:
                if C.capture_facts(self.root, recipe) != self._fact_pins[recipe['path']]:
                    raise ValueError(f'FACT_CHANGED during producer: {recipe["path"]}')

    def __enter__(self):
        if threading.active_count() != 1 or not _LOCK.acquire(blocking=False):
            raise ValueError('Observation requires an isolated single-threaded producer')
        self._original_open, self._original_io = builtins.open, io.open
        self._original_from_path = FactManifest.__dict__['from_path']
        self._original_resolve = FactManifest.resolve
        self._original_flag = FactManifest.acceptance_flag
        try:
            for path in self.explicit:
                self._file(path)
            builtins.open = lambda *a, **kw: self._open(self._original_open, *a, **kw)
            io.open = lambda *a, **kw: self._open(self._original_io, *a, **kw)
            FactManifest.from_path = classmethod(lambda cls, path: self._from_path(cls, path))
            FactManifest.resolve = lambda instance, fact_id, **kw: self._resolve(instance, fact_id, **kw)
            FactManifest.acceptance_flag = lambda instance, flag_id: self._flag(instance, flag_id)
            return self
        except BaseException:
            self._restore()
            raise

    def _restore(self):
        builtins.open, io.open = self._original_open, self._original_io
        FactManifest.from_path = self._original_from_path
        FactManifest.resolve = self._original_resolve
        FactManifest.acceptance_flag = self._original_flag
        _LOCK.release()

    def __exit__(self, exc_type, exc, traceback):
        self._restore()
        self.files = [{'path': p, 'hash_basis': 'raw'} for p in sorted(self._file_pins)]
        if exc_type is None:
            self.verify_unchanged()
