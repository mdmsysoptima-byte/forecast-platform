"""Synthetic demo data (section 13, demo configuration).

36 months of history, 200 series, drivers, budget and closed prior cycles so
that the accuracy and FVA screens have real numbers to show.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SEED = 20261012
HISTORY_MONTHS = 36

CATEGORIES = [
    ("C10", "İçecek", [("S101", "Gazlı içecek"), ("S102", "Maden suyu"), ("S103", "Meyve suyu")]),
    ("C20", "Atıştırmalık", [("S201", "Bisküvi"), ("S202", "Çikolata"), ("S203", "Çerez")]),
    ("C30", "Temizlik", [("S301", "Çamaşır"), ("S302", "Bulaşık")]),
    ("C40", "Kişisel bakım", [("S401", "Saç bakım"), ("S402", "Ağız bakım")]),
]

REGIONS = [
    ("R1", "Marmara", [("IST", "İstanbul"), ("BRS", "Bursa")]),
    ("R2", "İç Anadolu", [("ANK", "Ankara"), ("KON", "Konya"), ("ESK", "Eskişehir")]),
]

# Typical shelf price band per category, in TRY at the first history period.
PRICE_BAND = {"C10": (18, 70), "C20": (25, 160), "C30": (80, 400), "C40": (45, 260)}

CITY_WEIGHT = {"IST": 0.42, "BRS": 0.13, "ANK": 0.24, "KON": 0.12, "ESK": 0.09}

# Monthly seasonal profile per category, January to December.
SEASON = {
    "C10": [0.78, 0.76, 0.86, 0.95, 1.08, 1.25, 1.38, 1.34, 1.08, 0.92, 0.82, 0.78],
    "C20": [0.95, 0.92, 1.02, 1.08, 1.02, 0.96, 0.92, 0.95, 1.05, 1.06, 1.02, 1.05],
    "C30": [1.02, 0.98, 1.08, 1.06, 1.04, 0.98, 0.94, 0.92, 1.02, 1.04, 0.98, 0.94],
    "C40": [0.98, 0.96, 1.02, 1.04, 1.02, 1.0, 0.98, 0.96, 1.02, 1.04, 1.0, 0.98],
}


@dataclass
class DemoData:
    product_rows: list[dict] = field(default_factory=list)
    geo_rows: list[dict] = field(default_factory=list)
    series: list[dict] = field(default_factory=list)
    actuals: list[dict] = field(default_factory=list)   # series_code pair + month label
    drivers: dict[str, list[dict]] = field(default_factory=dict)
    budget: list[dict] = field(default_factory=list)
    promo_months: dict[str, list[str]] = field(default_factory=dict)
    prices: dict[str, float] = field(default_factory=dict)  # sku code -> base price
    meta: dict = field(default_factory=dict)


def month_labels(start_year: int, start_month: int, count: int) -> list[str]:
    out = []
    y, m = start_year, start_month
    for _ in range(count):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def build(current_period: str = "2026-10", horizon: int = 18) -> DemoData:
    rng = np.random.default_rng(SEED)
    data = DemoData()

    cy, cm = (int(x) for x in current_period.split("-"))
    hist_start_index = (cy * 12 + cm - 1) - HISTORY_MONTHS
    hist_labels = month_labels(hist_start_index // 12, hist_start_index % 12 + 1, HISTORY_MONTHS)
    future_labels = month_labels(cy, cm, horizon)

    # Product tree: 4 categories, 10 subcategories, 40 skus
    skus: list[tuple[str, str, str]] = []  # (sku_code, subcat_code, cat_code)
    sku_no = 1
    for cat_code, cat_name, subs in CATEGORIES:
        for sub_code, sub_name in subs:
            per_sub = 4
            for _ in range(per_sub):
                sku_code = f"SKU{sku_no:03d}"
                sku_name = f"{sub_name} {sku_no:03d}"
                low, high = PRICE_BAND[cat_code]
                price = round(float(rng.uniform(low, high)), 2)
                data.prices[sku_code] = price
                data.product_rows.append(
                    {
                        "category": (cat_code, cat_name),
                        "subcategory": (sub_code, sub_name),
                        # Third element carries member attributes; the base price makes
                        # the derived revenue measure (units * price) possible.
                        "sku": (sku_code, sku_name, {"base_price": price}),
                    }
                )
                skus.append((sku_code, sub_code, cat_code))
                sku_no += 1
    # 10 subcategories * 4 = 40 skus

    # Geography tree
    cities: list[tuple[str, str]] = []
    for reg_code, reg_name, cs in REGIONS:
        for city_code, city_name in cs:
            data.geo_rows.append(
                {
                    "country": ("TR", "Türkiye"),
                    "region": (reg_code, reg_name),
                    "city": (city_code, city_name),
                }
            )
            cities.append((city_code, reg_code))

    # Series and history
    actual_index: dict[tuple[str, str, str], float] = {}
    for sku_code, sub_code, cat_code in skus:
        sku_scale = float(rng.lognormal(mean=6.6, sigma=0.45))
        growth = float(rng.normal(0.004, 0.004))
        promo = [lbl for lbl in hist_labels if rng.random() < 0.07]
        data.promo_months[sku_code] = promo
        for city_code, _reg in cities:
            base = sku_scale * CITY_WEIGHT[city_code] * float(rng.normal(1.0, 0.12))
            base = max(base, 25.0)
            data.series.append({"sku": sku_code, "city": city_code})
            for t, lbl in enumerate(hist_labels):
                month = int(lbl.split("-")[1])
                season = SEASON[cat_code][month - 1]
                level = base * pow(1.0 + growth, t)
                value = level * season
                if lbl in promo:
                    value *= float(rng.uniform(1.25, 1.7))
                if month in (3, 4) and cat_code in ("C20", "C40"):
                    value *= 1.08  # pre holiday stocking
                value *= float(rng.normal(1.0, 0.07))
                v = round(max(0.0, value), 1)
                data.actuals.append({"sku": sku_code, "city": city_code, "bucket": lbl, "value": v})
                actual_index[(sku_code, city_code, lbl)] = v

    # Butce: gecen yil artı hedeflenen buyume. Yalnizca gelecek icin uretmek
    # yanlisti; butce yillik bir plandir, kapanmis donemleri de kapsar ve butce
    # farkinin gecmise bakabilmesi buna bagli. Son 12 gecmis ay da uretiliyor.
    budget_labels = hist_labels[-12:] + future_labels
    for sku_code, city_code in ((s["sku"], s["city"]) for s in data.series):
        for lbl in budget_labels:
            y, m = (int(x) for x in lbl.split("-"))
            prior = actual_index.get((sku_code, city_code, f"{y - 1}-{m:02d}"))
            if prior is not None:
                data.budget.append(
                    {"sku": sku_code, "city": city_code, "bucket": lbl, "value": round(prior * 1.12, 1)}
                )

    # Drivers
    cpi, fx = 100.0, 34.0
    cpi_rows, fx_rows = [], []
    for lbl in hist_labels:
        cpi *= 1.0 + float(rng.normal(0.028, 0.006))
        fx *= 1.0 + float(rng.normal(0.022, 0.008))
        cpi_rows.append({"bucket": lbl, "value": round(cpi, 2), "is_actual": True})
        fx_rows.append({"bucket": lbl, "value": round(fx, 3), "is_actual": True})
    data.drivers["cpi"] = cpi_rows
    data.drivers["fx_usd"] = fx_rows

    promo_rows = []
    for sku_code, _sub, _cat in skus:
        for lbl in hist_labels:
            intensity = 1.0 if lbl in data.promo_months[sku_code] else 0.0
            if intensity:
                promo_rows.append({"bucket": lbl, "member": sku_code, "value": 1.0, "is_actual": True})
    data.drivers["promo_intensity"] = promo_rows

    data.meta = {
        "history_labels": hist_labels,
        "future_labels": future_labels,
        "cpi_last": round(cpi, 2),
        "fx_last": round(fx, 3),
        "series_count": len(data.series),
    }
    return data


def driver_set_future(meta: dict, profile: str) -> dict[str, list[dict]]:
    """Future driver values per saved assumption package."""
    future = meta["future_labels"]
    cpi, fx = meta["cpi_last"], meta["fx_last"]
    monthly = {
        "baz": (0.022, 0.018),
        "yuksek_enflasyon": (0.042, 0.035),
        "kur_soku": (0.026, 0.055),
    }[profile]
    cpi_rows, fx_rows = [], []
    c, f = cpi, fx
    for lbl in future:
        c *= 1.0 + monthly[0]
        f *= 1.0 + monthly[1]
        cpi_rows.append({"bucket": lbl, "value": round(c, 2), "entry_method": "pct_growth"})
        fx_rows.append({"bucket": lbl, "value": round(f, 3), "entry_method": "pct_growth"})
    return {"cpi": cpi_rows, "fx_usd": fx_rows}
