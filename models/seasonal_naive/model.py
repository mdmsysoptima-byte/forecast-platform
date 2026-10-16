"""Seasonal naive reference model. No drivers.

level(last 6 periods mean) * seasonal index(same period last year)
with a damped trend and an empirical interval.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.forecast_models.contract import PredictRequest, PredictResponse

SEASON = 12


class SeasonalNaive:
    code = "seasonal_naive"
    version = "1.0.0"
    supported_grains = ["month", "week"]
    supported_levels = {
        "product": ["sku", "subcategory", "category"],
        "geo": ["city", "region", "country"],
    }
    required_drivers: list[str] = []
    optional_drivers: list[str] = []
    output_has_interval = True

    def predict(self, req: PredictRequest) -> PredictResponse:
        hist = req.history
        out_rows: list[dict] = []
        warnings: list[str] = []
        damping = float(req.params.get("trend_damping", 0.6))

        if hist.empty:
            return PredictResponse(
                scores=pd.DataFrame(columns=["series_key", "bucket_id", "value", "lower", "upper"]),
                diagnostics={"warnings": ["Geçmiş veri bulunamadi"], "model": self.code},
            )

        for series_key, grp in hist.sort_values("bucket_id").groupby("series_key", sort=False):
            y = grp["value"].to_numpy(dtype=float)
            if len(y) < 4:
                warnings.append(f"{series_key[:8]} için yetersiz geçmiş, ortalama kullanıldı")
                level = float(y.mean()) if len(y) else 0.0
                season = np.ones(SEASON)
                resid = 0.25
            else:
                level = float(y[-6:].mean()) if len(y) >= 6 else float(y.mean())
                season = self._season_index(y)
                fitted = self._fit(y, season)
                resid = float(np.mean(np.abs(y[-len(fitted):] - fitted)) / max(level, 1e-6))
            trend = self._trend(y)

            for h, bucket_id in enumerate(req.future_buckets, start=1):
                s = season[(len(y) + h - 1) % SEASON]
                growth = (1.0 + trend * (damping ** h))
                value = max(0.0, level * s * growth)
                band = value * min(0.6, resid * (1 + 0.08 * h))
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
                "warnings": warnings[:20],
                "drivers_used": [],
            },
        )

    @staticmethod
    def _season_index(y: np.ndarray) -> np.ndarray:
        """Multiplicative index per position, aligned to index 0 of the history."""
        base = max(float(np.mean(y)), 1e-6)
        idx = np.ones(SEASON)
        for p in range(SEASON):
            positions = y[p::SEASON]
            if len(positions):
                idx[p] = float(np.mean(positions)) / base
        idx = np.clip(idx, 0.35, 2.5)
        return idx / float(np.mean(idx))

    @staticmethod
    def _trend(y: np.ndarray) -> float:
        if len(y) < 2 * SEASON:
            return 0.0
        recent = float(np.mean(y[-SEASON:]))
        prior = float(np.mean(y[-2 * SEASON : -SEASON]))
        if prior <= 0:
            return 0.0
        return float(np.clip((recent / prior - 1.0) / SEASON, -0.03, 0.03))

    def _fit(self, y: np.ndarray, season: np.ndarray) -> np.ndarray:
        n = min(len(y), SEASON)
        level = float(np.mean(y[-6:])) if len(y) >= 6 else float(np.mean(y))
        return np.array([level * season[(len(y) - n + i) % SEASON] for i in range(n)])
