"""Model contract (section 7).

The platform never looks inside a model. It only calls predict().
This build uses pandas DataFrames where the specification names polars; the
column contract is identical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import pandas as pd


@dataclass
class PredictRequest:
    history: pd.DataFrame      # series_key, bucket_id, value
    drivers: pd.DataFrame      # driver_code, bucket_id, member_id, value  (past and future)
    series_meta: pd.DataFrame  # series_key, dim_members, attributes
    future_buckets: list[int]  # ordered target bucket ids
    horizon: int
    grain: str
    level: dict[str, str]
    params: dict = field(default_factory=dict)


@dataclass
class PredictResponse:
    scores: pd.DataFrame            # series_key, bucket_id, value, lower?, upper?
    diagnostics: dict = field(default_factory=dict)


@runtime_checkable
class ForecastModel(Protocol):
    code: str
    version: str
    supported_grains: list[str]
    supported_levels: dict[str, list[str]]
    required_drivers: list[str]
    optional_drivers: list[str]
    output_has_interval: bool

    def predict(self, req: PredictRequest) -> PredictResponse: ...


class MissingDriverError(RuntimeError):
    """Raised when a required driver has no value in the scenario driver set."""
