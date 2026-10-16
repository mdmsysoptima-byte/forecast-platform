"""Local analytic store (SQLite file, separate from the application database).

Stands in for DuckDB in this build. The large fact tables live here and are
never copied into the application database; queries run in the store and the
results are cached by the caller.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Sequence

from app.core.config import settings

_LOCK = threading.Lock()

DDL = """
CREATE TABLE IF NOT EXISTS actual (
    series_key TEXT NOT NULL,
    bucket_id  INTEGER NOT NULL,
    value      REAL NOT NULL,
    loaded_at  TEXT NOT NULL,
    PRIMARY KEY (series_key, bucket_id)
);
CREATE TABLE IF NOT EXISTS run_result (
    run_id     INTEGER NOT NULL,
    series_key TEXT NOT NULL,
    bucket_id  INTEGER NOT NULL,
    value      REAL NOT NULL,
    lower      REAL,
    upper      REAL,
    PRIMARY KEY (run_id, series_key, bucket_id)
);
CREATE TABLE IF NOT EXISTS rule_effect (
    run_id     INTEGER NOT NULL,
    rule_id    INTEGER NOT NULL,
    series_key TEXT NOT NULL,
    bucket_id  INTEGER NOT NULL,
    delta      REAL NOT NULL,
    PRIMARY KEY (run_id, rule_id, series_key, bucket_id)
);
CREATE TABLE IF NOT EXISTS snapshot_value (
    cycle_id        INTEGER NOT NULL,
    snapshot_at     TEXT NOT NULL,
    layer_code      TEXT NOT NULL,
    series_key      TEXT NOT NULL,
    target_bucket_id INTEGER NOT NULL,
    lag             INTEGER NOT NULL,
    value           REAL NOT NULL,
    PRIMARY KEY (cycle_id, layer_code, series_key, target_bucket_id)
);
CREATE TABLE IF NOT EXISTS consensus_out (
    version_id INTEGER NOT NULL,
    series_key TEXT NOT NULL,
    bucket_id  INTEGER NOT NULL,
    value      REAL NOT NULL,
    written_at TEXT NOT NULL,
    PRIMARY KEY (version_id, series_key, bucket_id)
);
CREATE INDEX IF NOT EXISTS ix_actual_bucket ON actual(bucket_id);
CREATE INDEX IF NOT EXISTS ix_snapshot_lag ON snapshot_value(lag, layer_code);
"""


class LocalStore:
    backend = "local"

    def __init__(self, path: str | None = None) -> None:
        self.path = path or settings.store_path

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init_schema(self) -> None:
        with _LOCK, self._conn() as conn:
            conn.executescript(DDL)

    # Reads -----------------------------------------------------------------
    def read_series(
        self, series_keys: Sequence[str], bucket_from: int | None = None, bucket_to: int | None = None
    ) -> list[dict]:
        if not series_keys:
            return []
        out: list[dict] = []
        with self._conn() as conn:
            for chunk in _chunks(list(series_keys), 800):
                q = f"SELECT series_key, bucket_id, value FROM actual WHERE series_key IN ({_ph(chunk)})"
                args: list = list(chunk)
                if bucket_from is not None:
                    q += " AND bucket_id >= ?"
                    args.append(bucket_from)
                if bucket_to is not None:
                    q += " AND bucket_id <= ?"
                    args.append(bucket_to)
                out.extend(dict(r) for r in conn.execute(q, args))
        return out

    def read_actual_totals(self, series_keys: Sequence[str]) -> dict[int, float]:
        rows = self.read_series(series_keys)
        totals: dict[int, float] = {}
        for r in rows:
            totals[r["bucket_id"]] = totals.get(r["bucket_id"], 0.0) + r["value"]
        return totals

    def read_drivers(self, driver_codes: Sequence[str]) -> list[dict]:
        """Driver history lives in the application database for this build."""
        return []

    def read_run_result(self, run_id: int) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT series_key, bucket_id, value, lower, upper FROM run_result WHERE run_id = ?",
                    (run_id,),
                )
            ]

    def read_rule_effects(self, run_id: int) -> list[dict]:
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT rule_id, series_key, bucket_id, delta FROM rule_effect WHERE run_id = ?",
                    (run_id,),
                )
            ]

    def read_snapshots(
        self, cycle_ids: Sequence[int] | None = None, layer_codes: Sequence[str] | None = None
    ) -> list[dict]:
        q = "SELECT cycle_id, snapshot_at, layer_code, series_key, target_bucket_id, lag, value FROM snapshot_value"
        args: list = []
        where = []
        if cycle_ids:
            where.append(f"cycle_id IN ({_ph(cycle_ids)})")
            args.extend(cycle_ids)
        if layer_codes:
            where.append(f"layer_code IN ({_ph(layer_codes)})")
            args.extend(layer_codes)
        if where:
            q += " WHERE " + " AND ".join(where)
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(q, args)]

    def data_as_of(self) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(loaded_at) AS d FROM actual").fetchone()
        return row["d"] if row else None

    def max_actual_bucket(self) -> int | None:
        with self._conn() as conn:
            row = conn.execute("SELECT MAX(bucket_id) AS b FROM actual").fetchone()
        return row["b"] if row else None

    # Writes ----------------------------------------------------------------
    def write_actuals(self, rows: Sequence[dict]) -> int:
        with _LOCK, self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO actual(series_key, bucket_id, value, loaded_at) VALUES (?,?,?,?)",
                [(r["series_key"], r["bucket_id"], r["value"], r["loaded_at"]) for r in rows],
            )
        return len(rows)

    def write_scores(self, run_id: int, rows: Sequence[dict]) -> int:
        with _LOCK, self._conn() as conn:
            conn.execute("DELETE FROM run_result WHERE run_id = ?", (run_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO run_result(run_id, series_key, bucket_id, value, lower, upper)"
                " VALUES (?,?,?,?,?,?)",
                [
                    (run_id, r["series_key"], r["bucket_id"], r["value"], r.get("lower"), r.get("upper"))
                    for r in rows
                ],
            )
        return len(rows)

    def write_rule_effects(self, run_id: int, rows: Sequence[dict]) -> int:
        if not rows:
            return 0
        with _LOCK, self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO rule_effect(run_id, rule_id, series_key, bucket_id, delta)"
                " VALUES (?,?,?,?,?)",
                [(run_id, r["rule_id"], r["series_key"], r["bucket_id"], r["delta"]) for r in rows],
            )
        return len(rows)

    def write_snapshot(self, rows: Sequence[dict]) -> int:
        with _LOCK, self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO snapshot_value"
                "(cycle_id, snapshot_at, layer_code, series_key, target_bucket_id, lag, value)"
                " VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        r["cycle_id"],
                        r["snapshot_at"],
                        r["layer_code"],
                        r["series_key"],
                        r["target_bucket_id"],
                        r["lag"],
                        r["value"],
                    )
                    for r in rows
                ],
            )
        return len(rows)

    def write_consensus(self, rows: Sequence[dict]) -> int:
        with _LOCK, self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO consensus_out(version_id, series_key, bucket_id, value, written_at)"
                " VALUES (?,?,?,?,?)",
                [
                    (r["version_id"], r["series_key"], r["bucket_id"], r["value"], r["written_at"])
                    for r in rows
                ],
            )
        return len(rows)

    def create_bi_views(self) -> list[str]:
        """Star schema for Power BI and Looker."""
        with _LOCK, self._conn() as conn:
            conn.executescript(
                """
                DROP VIEW IF EXISTS fact_forecast;
                CREATE VIEW fact_forecast AS
                  SELECT version_id, series_key, bucket_id, 'consensus' AS layer, value
                  FROM consensus_out;
                DROP VIEW IF EXISTS fact_actual;
                CREATE VIEW fact_actual AS
                  SELECT series_key, bucket_id, value FROM actual;
                DROP VIEW IF EXISTS fact_accuracy_input;
                CREATE VIEW fact_accuracy_input AS
                  SELECT s.cycle_id, s.layer_code, s.series_key, s.target_bucket_id, s.lag,
                         s.value AS forecast, a.value AS actual
                  FROM snapshot_value s
                  LEFT JOIN actual a
                    ON a.series_key = s.series_key AND a.bucket_id = s.target_bucket_id;
                """
            )
        return ["fact_forecast", "fact_actual", "fact_accuracy_input"]

    def truncate_all(self) -> None:
        with _LOCK, self._conn() as conn:
            for t in ("actual", "run_result", "rule_effect", "snapshot_value", "consensus_out"):
                conn.execute(f"DELETE FROM {t}")


def _ph(items: Sequence) -> str:
    return ",".join("?" for _ in items)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
