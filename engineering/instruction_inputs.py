"""Bind current instruction inputs to a historical candidate, without approval.

The project supplies observations and their required checks. This module checks
identity and dependency drift; it never interprets physical gates or runs CAD.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


class InputBindingError(ValueError):
    pass


def value_digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise InputBindingError('INPUT_PATH: expected a repository-relative path')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise InputBindingError(f'INPUT_PATH: missing or outside repository: {relative}')
    return path


def file_pin(root: Path, path: Path) -> dict:
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root):
        raise InputBindingError('INPUT_PATH: outside repository')
    payload = path.read_bytes()
    return {'path': path.relative_to(root).as_posix(), 'bytes': len(payload),
            'sha256': hashlib.sha256(payload).hexdigest()}


def _verify_pin(root: Path, pin: dict, label: str) -> Path:
    if not isinstance(pin, dict):
        raise InputBindingError(f'{label}: missing pin')
    path = _path(root, pin.get('path'))
    if file_pin(root, path) != pin:
        raise InputBindingError(f'{label}: changed bytes/hash: {pin.get("path")}')
    return path


def validate_binding(root: Path, binding: dict, observations: dict,
                     required_checks: dict[str, list[str]], *, historical_document_paths=()) -> dict:
    root = Path(root).resolve()
    if not isinstance(binding, dict) or binding.get('schema') != 'instruction-input-binding-v1':
        raise InputBindingError('INPUT_BINDING: unsupported schema')
    expected = binding.get('observations')
    if (not isinstance(expected, dict) or not expected or not isinstance(observations, dict)
            or set(expected) != set(observations) or set(expected) != set(required_checks)
            or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in expected.values())
            or any(not isinstance(v, list) or not v or any(not isinstance(c, str) or not c.strip() for c in v)
                   for v in required_checks.values())):
        raise InputBindingError('INPUT_BINDING: missing/unclassified domain, digest or required checks')
    candidate = _verify_pin(root, binding.get('candidate'), 'CANDIDATE')
    manifest = json.loads(candidate.read_text(encoding='utf-8'))
    sources = manifest.get('source_inputs') if isinstance(manifest, dict) else None
    if not isinstance(sources, list) or not sources:
        raise InputBindingError('CANDIDATE_SOURCE: historical inputs must be declared')
    archives = binding.get('historical_documents', {})
    source_paths = {p.get('path') for p in sources if isinstance(p, dict)}
    if (not isinstance(archives, dict) or not set(archives) <= set(historical_document_paths)
            or not set(archives) <= source_paths):
        raise InputBindingError('HISTORICAL_DOCUMENT: undeclared historical document')
    eol_inputs = binding.get('historical_eol_inputs', {})
    if (not isinstance(eol_inputs, dict) or not set(eol_inputs) <= source_paths
            or set(eol_inputs) & set(archives)):
        raise InputBindingError('CANDIDATE_SOURCE: invalid EOL reconstruction declarations')
    seen = set()
    for pin in sources:
        if isinstance(pin, dict) and pin.get('path') in archives:
            archived = archives[pin['path']]
            path = _verify_pin(root, archived, 'HISTORICAL_DOCUMENT')
            if any(archived.get(k) != pin.get(k) for k in ('bytes', 'sha256')):
                raise InputBindingError('HISTORICAL_DOCUMENT: archive differs from the original manifest input')
        elif isinstance(pin, dict) and pin.get('path') in eol_inputs:
            eol = eol_inputs[pin['path']]
            if not isinstance(eol, dict) or set(eol) != {'historical_eol', 'git_lf_sha256'} or eol['historical_eol'] != 'CRLF':
                raise InputBindingError('CANDIDATE_SOURCE: unsupported EOL reconstruction')
            path = _path(root, pin['path'])
            raw = path.read_bytes()
            lf = raw.replace(b'\r\n', b'\n')
            historical = lf.replace(b'\n', b'\r\n')
            if (raw not in (lf, historical) or b'\r' in lf
                    or hashlib.sha256(lf).hexdigest() != eol['git_lf_sha256']
                    or hashlib.sha256(historical).hexdigest() != pin.get('sha256')
                    or len(historical) != pin.get('bytes')):
                raise InputBindingError(f'CANDIDATE_SOURCE: EOL reconstruction does not match both pins: {pin["path"]}')
        else:
            path = _verify_pin(root, pin, 'CANDIDATE_SOURCE')
        if path in seen:
            raise InputBindingError('CANDIDATE_SOURCE: duplicate input')
        seen.add(path)
    actual = {key: value_digest(value) for key, value in observations.items()}
    changed = sorted(k for k in actual if actual[k] != expected[k])
    if changed:
        checks = sorted({check for key in changed for check in required_checks[key]})
        raise InputBindingError(f'RECHECK_REQUIRED: {", ".join(changed)}; checks: {", ".join(checks)}')
    return {'schema': 'instruction-input-provenance-v1', 'status': 'INPUTS_UNCHANGED',
            'candidate': binding['candidate'], 'observations': actual,
            'historical_documents': archives,
            'historical_eol_inputs': eol_inputs,
            'physical_approval': False}
