"""Driver regression model.

Seasonal profile times a ridge regression on the driver set. Global drivers
apply to every series; scoped drivers are matched through the dimension member
carried in series_meta.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.forecast_models.contract import MissingDriverError, PredictRequest, PredictResponse

SEASON = 12
RIDGE = 1.0


class DriverRegression:
    code = "driver_regression"
    version = "1.0.0"
    supported_grains = ["month"]
    supported_levels = {"product": ["sku", "subcategory"], "geo": ["city", "region"]}
    required_drivers = ["cpi", "fx_usd"]
    optional_drivers = ["promo_intensity"]
    output_has_interval = True

    def predict(self, req: PredictRequest) -> PredictResponse:
        hist, drivers = req.history, req.drivers
        if hist.empty:
            return PredictResponse(
                scores=pd.DataFrame(columns=["series_key", "bucket_id", "value", "lower", "upper"]),
                diagnostics={"warnings": ["Geçmiş veri bulunamadi"], "model": self.code},
            )
        for code in self.required_drivers:
            if drivers.empty or code not in set(drivers["driver_code"]):
                raise MissingDriverError(f"Zorunlu sürücü eksik: {code}")

        g_global, g_scoped = self._index_drivers(drivers)
        meta = {r.series_key: r for r in req.series_meta.itertuples()} if not req.series_meta.empty else {}
        used = sorted(set(drivers["driver_code"]))
        out_rows: list[dict] = []
        warnings: list[str] = []

        for series_key, grp in hist.sort_values("bucket_id").groupby("series_key", sort=False):
            buckets = grp["bucket_id"].to_numpy(dtype=int)
            y = grp["value"].to_numpy(dtype=float)
            if len(y) < 12:
                warnings.append(f"{series_key[:8]} için 12 donemden kısa geçmiş, taban model kullanıldı")
            season = self._season_index(y)
            y_des = np.array([y[i] / season[i % SEASON] for i in range(len(y))])

            member_ids = self._member_ids(meta.get(series_key))
            X = self._design(buckets, g_global, g_scoped, member_ids, start_t=0)
            target = np.log1p(np.clip(y_des, 0, None))
            beta = self._ridge(X, target)
            fitted = X @ beta
            resid = float(np.std(target - fitted)) if len(target) > 2 else 0.2

            Xf = self._design(
                np.array(req.future_buckets, dtype=int), g_global, g_scoped, member_ids, start_t=len(y)
            )
            pred_des = np.expm1(Xf @ beta)
            for h, bucket_id in enumerate(req.future_buckets):
                s = season[(len(y) + h) % SEASON]
                value = float(max(0.0, pred_des[h] * s))
                band = value * min(0.6, (np.exp(resid) - 1) * (1 + 0.05 * (h + 1)))
                out_rows.append(
                    {
                        "series_key": series_key,
                        "bucket_id": int(bucket_id),
                        "value": round(value, 3),
                        "lower": round(max(0.0, value - band), 3),
                        "upper": round(value + band, 3),
                    }
                )

        return PredictResponse(
            scores=pd.DataFrame(out_rows),
            diagnostics={
                "model": self.code,
                "version": self.version,
                "series": int(hist["series_key"].nunique()),
                "drivers_used": used,
                "warnings": warnings[:20],
            },
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _index_drivers(drivers: pd.DataFrame):
        g_global: dict[str, dict[int, float]] = {}
        g_scoped: dict[str, dict[tuple[int, int], float]] = {}
        for row in drivers.itertuples():
            code = row.driver_code
            member = getattr(row, "member_id", None)
            if member is None or (isinstance(member, float) and np.isnan(member)):
                g_global.setdefault(code, {})[int(row.bucket_id)] = float(row.value)
            else:
                g_scoped.setdefault(code, {})[(int(member), int(row.bucket_id))] = float(row.value)
        return g_global, g_scoped

    @staticmethod
    def _member_ids(meta_row) -> list[int]:
        if meta_row is None:
            return []
        dm = getattr(meta_row, "dim_members", None) or {}
        if isinstance(dm, str):
            return []
        return [int(v) for v in dm.values()]

    def _design(self, buckets, g_global, g_scoped, member_ids, start_t: int) -> np.ndarray:
        cols = [np.ones(len(buckets))]
        cols.append(np.arange(start_t, start_t + len(buckets), dtype=float) / 12.0)
        for code in ("cpi", "fx_usd"):
            series = g_global.get(code, {})
            vals = np.array([series.get(int(b), np.nan) for b in buckets], dtype=float)
            vals = self._fill(vals)
            cols.append(np.log(np.clip(vals, 1e-6, None)))
        promo = g_scoped.get("promo_intensity", {})
        if promo:
            vals = []
            for b in buckets:
                hit = [promo[(m, int(b))] for m in member_ids if (m, int(b)) in promo]
                vals.append(float(np.mean(hit)) if hit else 0.0)
            cols.append(np.array(vals, dtype=float))
        return np.column_stack(cols)

    @staticmethod
    def _fill(v: np.ndarray) -> np.ndarray:
        if np.all(np.isnan(v)):
            return np.ones_like(v)
        out = v.copy()
        last = np.nan
        for i in range(len(out)):
            if np.isnan(out[i]):
                out[i] = last if not np.isnan(last) else np.nanmean(v)
            last = out[i]
        return out

    @staticmethod
    def _ridge(X: np.ndarray, y: np.ndarray) -> np.ndarray:
        n_feat = X.shape[1]
        reg = RIDGE * np.eye(n_feat)
        reg[0, 0] = 0.0
        try:
            return np.linalg.solve(X.T @ X + reg, X.T @ y)
        except np.linalg.LinAlgError:  # pragma: no cover
            return np.linalg.pinv(X) @ y

    @staticmethod
    def _season_index(y: np.ndarray) -> np.ndarray:
        base = max(float(np.mean(y)), 1e-6)
        idx = np.ones(SEASON)
        for p in range(SEASON):
            positions = [v for i, v in enumerate(y) if i % SEASON == p]
            if positions:
                idx[p] = float(np.mean(positions)) / base
        idx = np.clip(idx, 0.35, 2.5)
        return idx / float(np.mean(idx))
