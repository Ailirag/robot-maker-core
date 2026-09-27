"""Typed, fail-closed access to the repository's engineering facts (process core).

This module reads values from the current fact manifest.  It does not interpret
physical gates, holds, or release permissions; those remain owned by
``cad/validate_prototype_parameters.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Collection


class FactStatus(StrEnum):
    READY = "READY"
    MISSING = "MISSING"
    INSUFFICIENT_PROVENANCE = "INSUFFICIENT_PROVENANCE"
    AMBIGUOUS_DATUM = "AMBIGUOUS_DATUM"
    WRONG_UNIT = "WRONG_UNIT"
    WRONG_SCOPE = "WRONG_SCOPE"


@dataclass(frozen=True)
class FactResolution:
    fact_id: str
    status: FactStatus
    value: Any = None
    unit: str | None = None
    provenance: str | None = None
    source: str | None = None
    scope: str | None = None
    datum: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "provenance": self.provenance,
            "source": self.source,
            "scope": self.scope,
            "datum": self.datum,
            "detail": self.detail,
        }


class FactManifest:
    def __init__(self, manifest: dict[str, Any]):
        parameters = manifest.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ValueError("fact manifest 'parameters' must be an object")
        self._parameters = parameters
        self._acceptance_flags = manifest.get('acceptance_flags', {})

    def acceptance_flag(self, flag_id: str) -> dict[str, Any]:
        """Relay a boolean record; false, missing and malformed stay distinct."""
        if not isinstance(self._acceptance_flags, dict):
            raise ValueError("fact manifest 'acceptance_flags' must be an object")
        value = self._acceptance_flags.get(flag_id)
        state = ('missing' if flag_id not in self._acceptance_flags else
                 'recorded' if type(value) is bool else 'invalid')
        return {'flag_id': flag_id, 'state': state, 'value': value if state == 'recorded' else None,
                'json_path': '$.acceptance_flags.' + flag_id}

    def acceptance_flags(self) -> list[dict[str, Any]]:
        if not isinstance(self._acceptance_flags, dict):
            raise ValueError("fact manifest 'acceptance_flags' must be an object")
        return [self.acceptance_flag(flag_id) for flag_id in sorted(self._acceptance_flags)]

    @classmethod
    def from_path(cls, path: str | Path) -> "FactManifest":
        with Path(path).open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("fact manifest root must be an object")
        return cls(payload)

    def resolve(
        self,
        fact_id: str,
        *,
        expected_unit: str | None = None,
        allowed_provenance: Collection[str] | None = None,
        require_datum: bool = False,
        expected_scope: str | None = None,
    ) -> FactResolution:
        spec = self._parameters.get(fact_id)
        if not isinstance(spec, dict) or spec.get("value") is None:
            return FactResolution(fact_id, FactStatus.MISSING, detail="fact or value is missing")

        unit = spec.get("unit")
        provenance = spec.get("provenance")
        source = spec.get("source")
        scope = spec.get("scope") or spec.get("revision")
        datum = spec.get("datum") or spec.get("basis")
        common = {
            "value": spec.get("value"),
            "unit": unit,
            "provenance": provenance,
            "source": source,
            "scope": scope,
            "datum": datum,
        }

        if expected_unit is not None and unit != expected_unit:
            return FactResolution(
                fact_id,
                FactStatus.WRONG_UNIT,
                detail=f"expected unit {expected_unit!r}, got {unit!r}",
                **common,
            )
        if allowed_provenance is not None and provenance not in set(allowed_provenance):
            return FactResolution(
                fact_id,
                FactStatus.INSUFFICIENT_PROVENANCE,
                detail=f"provenance {provenance!r} is not allowed",
                **common,
            )
        if expected_scope is not None and scope != expected_scope:
            return FactResolution(
                fact_id,
                FactStatus.WRONG_SCOPE,
                detail=f"expected scope {expected_scope!r}, got {scope!r}",
                **common,
            )
        if require_datum and not isinstance(datum, str):
            return FactResolution(
                fact_id,
                FactStatus.AMBIGUOUS_DATUM,
                detail="explicit datum or basis is required",
                **common,
            )
        return FactResolution(fact_id, FactStatus.READY, **common)
