"""Cockpit overview, the derived revenue measure and period accuracy."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain.models import Measure, Member, PriceIndex, Series, TimeBucket
from app.services import accuracy, cycle_svc, measures


def overview(client, headers, **kw):
    body = {"horizon_periods": 12, "history_periods": 24, **kw}
    res = client.post("/api/v1/cockpit/overview", headers=headers, json=body)
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------- timeline
def test_actual_never_leaks_into_the_future(client, planner_headers):
    body = overview(client, planner_headers)
    t = body["timeline"]
    ci = t["current_index"]
    assert ci > 0, "current period zaman cizgisinde bulunamadi"

    actual = t["series"]["actual"]
    assert all(actual[i] is None for i in range(ci, len(actual))), "gelecek donemlere gerceklesen sizmis"
    assert any(v is not None for v in actual[:ci]), "gecmis gerceklesen bos"


def test_forecast_lines_cover_the_past_too(client, planner_headers):
    """Ekranın sorusu "geçmişte ne tahmin ettik, ne çıktı"; tahmin çizgileri
    mevcut dönemde başlayamaz. Geçmiş değerler snapshot'tan gelir."""
    t = overview(client, planner_headers)["timeline"]
    ci = t["current_index"]
    for code in ("statistical", "consensus"):
        s = t["series"][code]
        assert any(v is not None for v in s[:ci]), f"{code} gecmiste bos"
        assert any(v is not None for v in s[ci:]), f"{code} gelecekte bos"


def test_actual_and_consensus_meet_without_a_gap(client, planner_headers):
    """İki çizgi arasında bir dönemlik boşluk kalırsa grafik kopuk görünür."""
    t = overview(client, planner_headers)["timeline"]
    actual, consensus = t["series"]["actual"], t["series"]["consensus"]
    last_actual = max(i for i, v in enumerate(actual) if v is not None)
    assert consensus[last_actual] is not None, "devir noktasinda konsensus bos"
    assert consensus[last_actual + 1] is not None, "devirden sonra konsensus kopuk"


def test_current_bucket_is_flagged(client, planner_headers):
    body = overview(client, planner_headers)
    buckets = body["timeline"]["buckets"]
    current = [b for b in buckets if b["is_current"]]
    assert len(current) == 1
    assert buckets.index(current[0]) == body["timeline"]["current_index"]
    assert current[0]["flag"] == "fence"


def test_horizon_selection_changes_forecast_length(client, planner_headers):
    counts = {}
    for horizon in (6, 12, 18):
        body = overview(client, planner_headers, horizon_periods=horizon, history_periods=12)
        t = body["timeline"]
        # Konsensus artik gecmisi de kapsiyor; uzunluk yalnizca gelecek kismindan.
        future = t["series"]["consensus"][t["current_index"]:]
        counts[horizon] = sum(1 for v in future if v is not None)
        assert body["meta"]["horizon_periods"] == horizon
    assert counts[6] < counts[12] < counts[18]
    assert counts[18] == 18


def test_history_selection_changes_actual_length(client, planner_headers):
    short = overview(client, planner_headers, history_periods=12)
    long = overview(client, planner_headers, history_periods=36)
    n = lambda b: sum(1 for v in b["timeline"]["series"]["actual"] if v is not None)
    assert n(short) == 12
    assert n(long) > n(short)


def test_grain_rollup_preserves_the_total(client, planner_headers):
    totals = {}
    for grain in ("month", "quarter", "year"):
        body = overview(client, planner_headers, grain=grain, history_periods=12)
        totals[grain] = sum(v for v in body["timeline"]["series"]["consensus"] if v is not None)
    assert totals["quarter"] == pytest.approx(totals["month"], rel=1e-6)
    assert totals["year"] == pytest.approx(totals["month"], rel=1e-6)


def test_model_band_wraps_the_statistical_line(client, planner_headers):
    s = overview(client, planner_headers)["timeline"]["series"]
    pairs = [
        (lo, mid, hi)
        for lo, mid, hi in zip(s["lower"], s["statistical"], s["upper"])
        if lo is not None and mid is not None and hi is not None
    ]
    assert pairs, "model araligi hic uretilmedi"
    for lo, mid, hi in pairs:
        assert lo <= mid <= hi


# --------------------------------------------------- katman karsilastirma
def test_table_rows_are_actual_plus_numbered_models(client, planner_headers):
    body = overview(client, planner_headers)
    assert "kpis" not in body, "kartlar tabloya donustu"
    rows = body["table"]["rows"]
    assert [r["code"] for r in rows] == ["actual", "statistical", "sales", "planner", "consensus"]
    # Butce satir degil: snapshot'i yok, MAPE'si hesaplanamaz.
    # Pazarlama da yok: gelecekte hic degeri olmadigi icin satiri bos kaliyordu.
    assert {"budget", "marketing"}.isdisjoint(r["code"] for r in rows)
    # Ekranda katman adi gecmez; satirlar numaralidir, kaynak yalnizca ipucunda.
    assert [r["label"] for r in rows] == ["Gerçekleşen", "Model 1", "Model 2", "Model 3", "Model 4"]
    assert rows[0]["source"] is None
    assert all(r["source"] for r in rows[1:])


def test_actual_row_has_no_future_and_no_error(client, planner_headers):
    """Gerçekleşenin geleceği yoktur; MAPE'si de kendisiyle karşılaştırma olurdu."""
    row = next(r for r in overview(client, planner_headers)["table"]["rows"] if r["code"] == "actual")
    assert row["mape"] is None
    for key, cell in row["cells"].items():
        if key.endswith("_future"):
            assert cell["total"] is None, f"{key} dolu gelmemeli"
        else:
            assert cell["total"] > 0


def test_table_columns_split_into_total_and_average_only_above_month(client, planner_headers):
    monthly = overview(client, planner_headers)["table"]
    assert monthly["show_avg"] is False
    assert all(g["span"] == 1 for g in monthly["column_groups"])
    assert all(c["part"] == "total" for c in monthly["columns"])

    quarterly = overview(client, planner_headers, grain="quarter")["table"]
    assert quarterly["show_avg"] is True
    # MAPE ve bütçe farkı tek değer; toplam/ortalama diye bölünmezler.
    measure_groups = [g for g in quarterly["column_groups"] if g["key"] not in ("mape", "budget_gap")]
    assert measure_groups and all(g["span"] == 2 for g in measure_groups)

    # Ortalama, toplamin donem sayisina bolumudur.
    row = next(r for r in quarterly["rows"] if r["code"] == "consensus")
    cell = row["cells"]["units_future"]
    assert cell["avg"] == pytest.approx(cell["total"] / quarterly["future_periods"], rel=0.01)


def test_quarter_total_equals_month_total(client, planner_headers):
    """Kova degismesi toplami degistirmez, yalnizca nasil bolundugunu degistirir."""
    m = next(r for r in overview(client, planner_headers)["table"]["rows"] if r["code"] == "consensus")
    q = next(r for r in overview(client, planner_headers, grain="quarter")["table"]["rows"]
             if r["code"] == "consensus")
    assert q["cells"]["units_future"]["total"] == pytest.approx(
        m["cells"]["units_future"]["total"], rel=0.01)


def test_table_mape_matches_the_accuracy_endpoint(client, planner_headers):
    """Tablodaki MAPE ile Analiz ekranindaki ayni sayi olmali."""
    row = next(r for r in overview(client, planner_headers)["table"]["rows"] if r["code"] == "consensus")
    reported = client.get(
        "/api/v1/analysis/accuracy?layer=consensus&lag=1", headers=planner_headers
    ).json()["summary_metrics"]["mape"]
    assert row["mape"] == pytest.approx(reported, rel=0.02)


def test_partial_layers_are_flagged(client, planner_headers):
    """Katkici katmanlari tam tahmin degil; toplamlari ciplak okunmamali."""
    rows = {r["code"]: r for r in overview(client, planner_headers)["table"]["rows"]}
    assert rows["consensus"]["future_partial"] is False
    assert rows["consensus"]["future_coverage"] == pytest.approx(1.0, abs=0.02)
    assert rows["sales"]["future_partial"] is True
    assert rows["sales"]["future_coverage"] < 0.5


def test_only_the_budget_gap_chart_remains(client, planner_headers):
    body = overview(client, planner_headers)
    charts = {c["code"]: c for c in body["charts"]}
    assert set(charts) == {"budget_gap"}
    gap = [p for p in charts["budget_gap"]["points"] if p["gap"] is not None]
    assert gap, "butce farki noktasi yok"
    # The cumulative series must be the running sum of the period gaps.
    running = 0.0
    for p in gap:
        running += p["gap"]
        assert p["cumulative"] == pytest.approx(running, abs=0.5)


def test_budget_gap_chart_spans_the_whole_window(client, planner_headers):
    """Grafik bütçe verisinin kapsamına değil, seçilen pencereye oturmalı;
    aksi halde tarih aralığını genişletmek hiçbir şeyi değiştirmiyor görünür."""
    body = overview(client, planner_headers, window_mode="range",
                    start_label="2024-06", end_label="2028-03")
    labels = [b["label"] for b in body["timeline"]["buckets"]]
    chart = next(c for c in body["charts"] if c["code"] == "budget_gap")
    assert [p["label"] for p in chart["points"]] == labels
    # Bütçesi olmayan dönemler boş gelir, atılmaz.
    assert any(p["gap"] is None for p in chart["points"])
    assert any(p["gap"] is not None for p in chart["points"])


def test_table_has_a_budget_gap_column(client, planner_headers):
    t = overview(client, planner_headers)["table"]
    assert [c["key"] for c in t["columns"]][-2:] == ["mape", "budget_gap"]
    rows = {r["code"]: r for r in t["rows"]}
    # Gerçekleşenin geleceği yok, karşılaştıracak bir tahmin de yok.
    assert rows["actual"]["budget_gap"] is None
    # Tam katmanlar karşılaştırılır.
    assert rows["consensus"]["budget_gap"] is not None
    assert rows["statistical"]["budget_gap"] is not None
    # Kısmi örtüler karşılaştırılmaz: dolu olmayan hücreler sıfır sayılır ve
    # satır "bütçenin çok altında" gibi okunurdu.
    for r in t["rows"]:
        if r["future_partial"]:
            assert r["budget_gap"] is None, f"{r['label']} kismi ama butce farki uretilmis"


def test_budget_gap_column_uses_only_the_budgeted_periods(client, planner_headers):
    """Bütçe ufkun tamamını kapsamıyor; karşılaştırmayı tüm ufka yaymak modeli
    bütçenin çok üstünde gösterirdi."""
    t = overview(client, planner_headers)["table"]
    assert 0 < t["budget_periods"] <= t["future_periods"]
    consensus = next(r for r in t["rows"] if r["code"] == "consensus")
    # Konsensus butceye yakin seyreder; ufkun tamami kullanilsaydi yuzde 40'i
    # asardi (butce 12 donem, ufuk 18).
    assert abs(consensus["budget_gap"]) < 25


def test_budget_gap_reaches_into_the_past(client, planner_headers):
    """Bütçe yıllık bir plandır, kapanmış dönemleri de kapsar; fark grafiği
    yalnızca geleceğe bakarsa planın tutup tutmadığı hiç görünmez."""
    body = overview(client, planner_headers)
    chart = next(c for c in body["charts"] if c["code"] == "budget_gap")
    labels = [b["label"] for b in body["timeline"]["buckets"]]
    cut = body["timeline"]["current_index"]
    past_labels = set(labels[:cut])
    filled_past = [p for p in chart["points"] if p["gap"] is not None and p["label"] in past_labels]
    assert filled_past, "butce farki gecmise gitmiyor"


def test_forecast_window_is_never_shorter_than_history(client, planner_headers):
    """Kokpit bir tahmin ekranı: varsayılan görünümde gerçekleşen kuyruğu
    tahmin penceresinden uzun olmamalı, yoksa grafiğin ağırlığı geçmişe kayar."""
    # Yardımcı pencereyi sabitlediği için varsayılanlar boş gövdeyle sınanır.
    res = client.post("/api/v1/cockpit/overview", headers=planner_headers, json={})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["meta"]["history_periods"] <= body["meta"]["horizon_periods"]
    assert max(body["options"]["histories"]) <= max(body["options"]["horizons"])


def test_options_drive_the_selectors(client, planner_headers):
    opts = overview(client, planner_headers)["options"]
    assert {m["code"] for m in opts["measures"]} == {"units", "revenue"}
    assert [g["code"] for g in opts["grains"]] == ["month", "quarter", "year"]
    assert opts["horizons"] and max(opts["horizons"]) <= 18
    assert opts["scopes"], "kapsam secenegi uretilmedi"
    assert all(s["nodes"] for s in opts["scopes"])
    assert [m["code"] for m in opts["window_modes"]] == ["periods", "range"]
    assert len(opts["periods"]) > 24, "tarih araligi secenekleri uretilmedi"


# ------------------------------------------------------------------ pencere
def test_date_range_mode_sets_both_ends(client, planner_headers):
    body = overview(client, planner_headers, window_mode="range",
                    start_label="2026-01", end_label="2027-06")
    meta = body["meta"]
    assert meta["window_mode"] == "range"
    assert meta["start_label"] == "2026-01"
    assert meta["end_label"] == "2027-06"
    labels = [b["label"] for b in body["timeline"]["buckets"]]
    assert labels[0] == "2026-01" and labels[-1] == "2027-06"


def test_both_window_modes_describe_the_same_window(client, planner_headers):
    """Aynı pencere iki ayrı biçimde ifade edilince aynı sayıyı vermeli."""
    by_count = overview(client, planner_headers, history_periods=6, horizon_periods=6)
    span = by_count["meta"]
    by_range = overview(client, planner_headers, window_mode="range",
                        start_label=span["start_label"], end_label=span["end_label"])
    a = next(r for r in by_count["table"]["rows"] if r["code"] == "consensus")
    b = next(r for r in by_range["table"]["rows"] if r["code"] == "consensus")
    assert a["cells"]["units_future"]["total"] == pytest.approx(
        b["cells"]["units_future"]["total"], rel=0.001)


# ------------------------------------------------------------------- kapsam
def test_scope_narrows_the_error_metric_too(client, planner_headers):
    """Kapsam daraldığında MAPE de daralmalı; aksi halde aynı satırda daralmış
    hacim ile tüm şirketin hatası yan yana durur ve yanlış okunur."""
    full = overview(client, planner_headers)
    dim = full["options"]["scopes"][0]
    node = dim["nodes"][0]
    narrow = overview(client, planner_headers,
                      scope={dim["dimension"]: {"nodes": [node["node_id"]]}})

    assert narrow["meta"]["series_in_scope"] < full["meta"]["series_in_scope"]
    a = next(r for r in full["table"]["rows"] if r["code"] == "statistical")
    b = next(r for r in narrow["table"]["rows"] if r["code"] == "statistical")
    assert b["cells"]["units_future"]["total"] < a["cells"]["units_future"]["total"]
    assert b["mape"] is not None
    assert b["mape"] != a["mape"], "MAPE kapsami takip etmiyor"


# ------------------------------------------------------------- scope, roles
def test_scope_narrows_the_series_set(client, planner_headers):
    full = overview(client, planner_headers)
    scopes = full["options"]["scopes"][0]
    node = scopes["nodes"][0]["node_id"]
    narrowed = overview(client, planner_headers, scope={scopes["dimension"]: {"nodes": [node]}})
    assert narrowed["meta"]["series_in_scope"] < full["meta"]["series_in_scope"]
    full_total = sum(v for v in full["timeline"]["series"]["consensus"] if v is not None)
    part_total = sum(v for v in narrowed["timeline"]["series"]["consensus"] if v is not None)
    assert 0 < part_total < full_total


def test_sales_contributor_only_sees_its_own_scope(client, sales_headers, planner_headers):
    sales = overview(client, sales_headers)
    planner = overview(client, planner_headers)
    assert sales["meta"]["restricted"] is True
    assert planner["meta"]["restricted"] is False
    assert sales["meta"]["series_in_scope"] < planner["meta"]["series_in_scope"]


def test_cockpit_requires_a_session(client):
    assert client.post("/api/v1/cockpit/overview", json={}).status_code == 401


# ----------------------------------------------------------------- revenue
def test_revenue_is_units_times_price(db):
    """The derived measure multiplies the stored volume by the member price."""
    cycle = cycle_svc.active_cycle(db)
    current = db.get(TimeBucket, cycle.current_period_bucket_id)
    series = db.execute(select(Series).limit(1)).scalars().one()
    sku_code = series.dim_codes["product"]
    member = db.execute(select(Member).where(Member.code == sku_code)).scalars().one()
    base_price = member.attributes["base_price"]
    assert base_price > 0

    factors = measures.price_factors(db, "revenue", {series.series_key}, [current.id])
    # The base of the index is the current period, so today's factor is the price.
    assert factors[(series.series_key, current.id)] == pytest.approx(base_price, rel=1e-6)
    assert measures.price_factors(db, "units", {series.series_key}, [current.id]) == {}
    assert measures.base_measure_of(db, "revenue") == "units"
    assert measures.is_derived(db, "units") is False


def test_revenue_tracks_volume_and_inflation(client, planner_headers):
    units = overview(client, planner_headers, measure="units")
    revenue = overview(client, planner_headers, measure="revenue")
    u = sum(v for v in units["timeline"]["series"]["consensus"] if v is not None)
    r = sum(v for v in revenue["timeline"]["series"]["consensus"] if v is not None)
    assert r > u, "ciro hacimden buyuk olmali"
    assert revenue["meta"]["unit_type"] == "value"
    # Real TRY deflates by the price index, so it must be below nominal.
    real = overview(client, planner_headers, measure="revenue", view="real")
    r_real = sum(v for v in real["timeline"]["series"]["consensus"] if v is not None)
    assert 0 < r_real < r


def test_price_index_covers_the_forecast_window(db):
    """Without forward index rows the real and fx views would silently do nothing."""
    cycle = cycle_svc.active_cycle(db)
    window = cycle_svc.forecast_window(db, cycle)
    covered = {
        (r.date.year, r.date.month)
        for r in db.execute(select(PriceIndex).where(PriceIndex.index_code == "tufe")).scalars()
    }
    missing = [b.label for b in window if (b.start_date.year, b.start_date.month) not in covered]
    assert not missing, f"fiyat endeksi eksik donemler: {missing}"


def test_grid_screen_also_serves_the_derived_measure(client, planner_headers):
    def total(measure):
        res = client.post("/api/v1/grid/query", headers=planner_headers, json={
            "version_id": 1, "level": {"product": "category"}, "layers": ["consensus"],
            "measure": measure, "history_periods": 0,
        })
        assert res.status_code == 200, res.text
        return sum(sum(r["layers"]["consensus"].values()) for r in res.json()["rows"])

    assert total("revenue") > total("units")


# ---------------------------------------------------------------- accuracy
def test_accuracy_by_period_is_ordered_and_bounded(db):
    rows = accuracy.accuracy_by_period(db, "consensus", lag=1, limit=12)
    assert rows, "donem bazinda dogruluk uretilmedi"
    assert len(rows) <= 12
    assert rows == sorted(rows, key=lambda r: r["seq"])
    for r in rows:
        assert r["wmape"] >= 0
        assert r["n"] > 0
        assert r["label"]


def test_cockpit_carries_only_the_forecast_payload(client, planner_headers):
    """Operational lists live on their own screens, not on the cockpit."""
    body = overview(client, planner_headers)
    for key in ("cycle", "version", "options", "timeline", "table", "charts", "meta"):
        assert key in body, f"{key} kokpit yanıtında yok"
    for key in ("alerts", "recent_runs", "pending_approvals", "biggest_changes", "steps"):
        assert key not in body, f"{key} kokpitten kaldırılmalıydı"


def test_removed_cockpit_endpoints_are_gone(client, planner_headers):
    assert client.get("/api/v1/cockpit/operations", headers=planner_headers).status_code == 404
    assert client.get("/api/v1/cockpit", headers=planner_headers).status_code == 404
