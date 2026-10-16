"""Single analytic store interface. Every backend implements the same methods."""
from __future__ import annotations

from typing import Protocol, Sequence


class StoreAdapter(Protocol):
    """Section 3: read_series, read_drivers, write_scores, write_consensus."""

    def init_schema(self) -> None: ...

    # Reads -----------------------------------------------------------------
    def read_series(
        self, series_keys: Sequence[str], bucket_from: int | None = None, bucket_to: int | None = None
    ) -> list[dict]:
        """Actual history rows: series_key, bucket_id, value."""
        ...

    def read_drivers(self, driver_codes: Sequence[str]) -> list[dict]: ...

    def read_run_result(self, run_id: int) -> list[dict]: ...

    def read_rule_effects(self, run_id: int) -> list[dict]: ...

    def read_snapshots(
        self, cycle_ids: Sequence[int] | None = None, layer_codes: Sequence[str] | None = None
    ) -> list[dict]: ...

    def data_as_of(self) -> str | None: ...

    # Writes ----------------------------------------------------------------
    def write_actuals(self, rows: Sequence[dict]) -> int: ...

    def write_scores(self, run_id: int, rows: Sequence[dict]) -> int: ...

    def write_rule_effects(self, run_id: int, rows: Sequence[dict]) -> int: ...

    def write_snapshot(self, rows: Sequence[dict]) -> int: ...

    def write_consensus(self, rows: Sequence[dict]) -> int: ...

    def create_bi_views(self) -> list[str]:
        """Star schema views: fact_forecast, dim_*."""
        ...
