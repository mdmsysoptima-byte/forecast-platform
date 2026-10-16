"""Store adapter factory. One interface, several implementations."""
from __future__ import annotations

from app.adapters.local_store import LocalStore
from app.core.config import settings

_STORE: LocalStore | None = None


def get_store():
    """Return the configured analytic store.

    STORE_BACKEND=local uses the bundled SQLite store. The bigquery and mssql
    adapters declare the same interface and are not implemented in this build
    (see adapters/warehouse_stubs.py and README, Varsayimlar).
    """
    global _STORE
    if settings.store_backend in ("bigquery", "mssql"):
        from app.adapters.warehouse_stubs import make_warehouse_store

        return make_warehouse_store(settings.store_backend)
    if _STORE is None:
        _STORE = LocalStore()
        _STORE.init_schema()
    return _STORE
