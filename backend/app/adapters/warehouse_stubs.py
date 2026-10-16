"""BigQuery and MS SQL Server adapters.

Interface only in this build. Section 15 of the specification says that an
out of scope need is declared as an interface and marked TODO instead of being
half implemented. Both adapters must pass the same test set as LocalStore
before they are switched on.
"""
from __future__ import annotations

from typing import Sequence

_NOT_READY = (
    "Bu derlemede {backend} adaptörü bağlanmadı. STORE_BACKEND=local ile çalıştırın. "
    "Adaptör arayüzü app/adapters/base.py içinde tanımlı."
)


class WarehouseStore:
    def __init__(self, backend: str) -> None:
        self.backend = backend

    def _todo(self, method: str):
        raise NotImplementedError(f"{_NOT_READY.format(backend=self.backend)} (TODO: {method})")

    def init_schema(self) -> None:
        self._todo("init_schema")

    def read_series(self, series_keys: Sequence[str], bucket_from=None, bucket_to=None):
        self._todo("read_series")

    def read_drivers(self, driver_codes: Sequence[str]):
        self._todo("read_drivers")

    def read_run_result(self, run_id: int):
        self._todo("read_run_result")

    def read_rule_effects(self, run_id: int):
        self._todo("read_rule_effects")

    def read_snapshots(self, cycle_ids=None, layer_codes=None):
        self._todo("read_snapshots")

    def data_as_of(self):
        self._todo("data_as_of")

    def write_actuals(self, rows):
        self._todo("write_actuals")

    def write_scores(self, run_id: int, rows):
        self._todo("write_scores")

    def write_rule_effects(self, run_id: int, rows):
        self._todo("write_rule_effects")

    def write_snapshot(self, rows):
        self._todo("write_snapshot")

    def write_consensus(self, rows):
        self._todo("write_consensus")

    def create_bi_views(self):
        self._todo("create_bi_views")


def make_warehouse_store(backend: str) -> WarehouseStore:
    return WarehouseStore(backend)
