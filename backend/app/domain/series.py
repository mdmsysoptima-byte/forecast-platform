"""Deterministic series key.

series_key = sha1(sorted "dimension_code:member_code" pairs + measure code).
The analytic store and the application database talk through this key only.
"""
from __future__ import annotations

import hashlib


def make_series_key(dim_codes: dict[str, str], measure_code: str) -> str:
    parts = [f"{d}:{m}" for d, m in sorted(dim_codes.items())]
    parts.append(f"measure:{measure_code}")
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def scope_hash(scope: dict) -> str:
    raw = repr(sorted((k, sorted(v) if isinstance(v, list) else v) for k, v in scope.items()))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
