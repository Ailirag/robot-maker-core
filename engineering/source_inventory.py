"""Explicit file membership for projections, including Git-free snapshots."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path, PurePosixPath

SCHEMA = 'engineering-source-inventory-v1'


def _name(value):
    if (not isinstance(value, str) or not value or ':' in value or '\\' in value
            or any(p in ('', '.', '..') or p.casefold() == '.git' for p in value.split('/'))):
        raise ValueError(f'INVENTORY_PATH: invalid relative file name {value!r}')
    return value


class FileInventory:
    def __init__(self, root, paths):
        self.root = Path(root).resolve()
        if not isinstance(paths, list):
            raise ValueError('INVENTORY: paths must be a list')
        names = [_name(p) for p in paths]
        if len({p.casefold() for p in names}) != len(names):
            raise ValueError('INVENTORY: duplicate or case-ambiguous paths')
        self.paths = tuple(sorted(names))
        self._names = frozenset(names)

    @classmethod
    def load(cls, root, manifest):
        try:
            root = Path(root).resolve()
            target = root / _name(manifest)
            if not target.resolve().is_relative_to(root):
                raise ValueError('INVENTORY: manifest escapes root')
            data = json.loads(target.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or set(data) != {'schema','paths'} or data['schema'] != SCHEMA:
                raise ValueError('INVENTORY: unsupported declaration')
            return cls(root, data['paths'])
        except (OSError, ValueError) as exc:
            raise ValueError(f'INVENTORY: {exc}') from exc

    def relative(self, path):
        path = Path(path)
        if path.is_absolute():
            try:
                name = path.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise ValueError('INVENTORY_PATH: outside root') from exc
        else:
            name = path.as_posix()
        _name(name)
        if not (self.root/name).resolve().is_relative_to(self.root):
            raise ValueError('INVENTORY_PATH: resolved path escapes root')
        return name

    def require(self, path):
        name = self.relative(path)
        if name not in self._names:
            raise ValueError(f'INVENTORY_UNDECLARED: {name}')
        target = self.root/name
        if not target.is_file():
            raise ValueError(f'INVENTORY_MISSING: {name}')
        return target

    def contains(self, path):
        name = self.relative(path)
        if name not in self._names:
            return False
        self.require(name)
        return True

    def files(self, folder, pattern='*', *, recursive=True):
        parent = PurePosixPath(_name(folder))
        for name in self.paths:
            path = PurePosixPath(name)
            if (parent in path.parents and (recursive or path.parent == parent)
                    and fnmatch.fnmatchcase(path.name, pattern)):
                yield self.require(name)
