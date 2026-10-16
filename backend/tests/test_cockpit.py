"""Cockpit overview, the derived revenue measure and period accuracy."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.domain.models import Measure, Member, PriceIndex, Series, TimeBucket
from app.services import accuracy, cycle_svc, measures


ALL_MODELS = ["statistical", "sales", "planner", "consensus"]


def overview(client, headers, **kw):
    body = {"horizon_periods": 12, "history_periods": 24, "models": ALL_MODELS, **kw}
    res = client.post("/api/v1/cockpit/overview", headers=headers, json=body)
    assert res.status_code == 200, res.text
    return res.json()


def model_points(body, code):
    """Zaman çizgisindeki bir modelin nokta dizisi."""
    return next(m["points"] for m in body["timeline"]["models"] if m["code"] == code)


def table_row(body, code):
    return next(r for r in body["table"]["rows"] if r["code"] == code)


def accuracy_row(body, code):
    return next(r for r in body["accuracy"]["rows"] if r["code"] == code)


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
    body = overview(client, planner_headers)
    ci = body["timeline"]["current_index"]
    for code in ALL_MODELS:
        s = model_points(body, code)
        assert any(v is not None for v in s[:ci]), f"{code} gecmiste bos"
        assert any(v is not None for v in s[ci:]), f"{code} gelecekte bos"


def test_every_selected_model_is_a_full_curve(client, planner_headers):
    """Katkıcı katmanları gelecekte seyrek; ham okunsa Model 2 bugünden sonra
    dibe çökerdi. Zincir boyunca düşülerek hepsi tam bir tahmin olur."""
    body = overview(client, planner_headers)
    filled = {m["code"]: sum(1 for v in m["points"] if v is not None)
              for m in body["timeline"]["models"]}
    assert set(filled) == set(ALL_MODELS)
    assert len(set(filled.values())) == 1, f"modeller ayni uzunlukta degil: {filled}"

    # Gelecek toplamlari ayni buyukluk mertebesinde; biri otekinin yuzde 5'i olamaz.
    totals = [table_row(body, c)["cells"]["units_future"] for c in ALL_MODELS]
    assert min(totals) > 0.5 * max(totals), f"model toplamlari ayrisiyor: {totals}"


def test_models_and_actual_meet_without_a_gap(client, planner_headers):
    """İki çizgi arasında bir dönemlik boşluk kalırsa grafik kopuk görünür."""
    body = overview(client, planner_headers)
    actual = body["timeline"]["series"]["actual"]
    last_actual = max(i for i, v in enumerate(actual) if v is not None)
    for code in ALL_MODELS:
        s = model_points(body, code)
        assert s[last_actual] is not None, f"{code} devir noktasinda bos"
        assert s[last_actual + 1] is not None, f"{code} devirden sonra kopuk"


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
        # Modeller artik gecmisi de kapsiyor; uzunluk yalnizca gelecek kismindan.
        future = model_points(body, "consensus")[body["timeline"]["current_index"]:]
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
        totals[grain] = sum(v for v in model_points(body, "consensus") if v is not None)
    assert totals["quarter"] == pytest.approx(totals["month"], rel=1e-6)
    assert totals["year"] == pytest.approx(totals["month"], rel=1e-6)


def test_model_band_wraps_the_statistical_line(client, planner_headers):
    body = overview(client, planner_headers)
    s = body["timeline"]["series"]
    stat = model_points(body, "statistical")
    pairs = [
        (lo, mid, hi)
        for lo, mid, hi in zip(s["lower"], stat, s["upper"])
        if lo is not None and mid is not None and hi is not None
    ]
    assert pairs, "model araligi hic uretilmedi"
    for lo, mid, hi in pairs:
        assert lo <= mid <= hi


def test_band_disappears_when_its_model_is_not_selected(client, planner_headers):
    """Belirsizlik bandı modelin ham çıktısına ait; o model seçili değilse band
    başka bir çizgiyi sarıyormuş gibi görünürdü."""
    body = overview(client, planner_headers, models=["consensus"])
    s = body["timeline"]["series"]
    assert all(v is None for v in s["lower"])
    assert all(v is None for v in s["upper"])


# --------------------------------------------------- model karsilastirma
def test_table_rows_are_actual_plus_the_selected_models(client, planner_headers):
    body = overview(client, planner_headers)
    assert "kpis" not in body, "kartlar tabloya donustu"
    rows = body["table"]["rows"]
    assert [r["code"] for r in rows] == ["actual", *ALL_MODELS]
    # Ekranda katman adi gecmez; satirlar numaralidir.
    assert [r["label"] for r in rows] == ["Gerçekleşen", "Model 1", "Model 2", "Model 3", "Model 4"]
    # MAPE ve butce farki burada degil, kendi tablosunda.
    assert all("mape" not in r and "budget_gap" not in r for r in rows)


def test_measure_columns_are_grouped_into_past_and_future(client, planner_headers):
    """Ölçü üst başlık, altında geçmiş ve gelecek; dört düz sütun hangi ikisinin
    aynı ölçüye ait olduğunu göstermiyordu."""
    t = overview(client, planner_headers)["table"]
    assert [g["label"] for g in t["column_groups"]] == ["Adet", "Ciro"]
    assert all(g["span"] == 2 for g in t["column_groups"])
    assert [c["label"] for c in t["columns"]] == ["Geçmiş", "Gelecek", "Geçmiş", "Gelecek"]
    # Alt sutunlar ust basliga baglanir, yoksa gruplama hizalanmaz.
    assert [c["group"] for c in t["columns"]] == ["units", "units", "revenue", "revenue"]
    assert sum(g["span"] for g in t["column_groups"]) == len(t["columns"])


def test_model_selection_drives_table_and_chart_together(client, planner_headers):
    """Filtre tek bir seçim; tablo ile grafik ayrı şeyler gösteremez."""
    body = overview(client, planner_headers, models=["statistical", "consensus"])
    picked = ["statistical", "consensus"]
    assert body["meta"]["models"] == picked
    assert [r["code"] for r in body["table"]["rows"]] == ["actual", *picked]
    assert [m["code"] for m in body["timeline"]["models"]] == picked
    assert [r["code"] for r in body["accuracy"]["rows"]] == picked


def test_empty_model_selection_falls_back(client, planner_headers):
    """Hiç model seçilmezse ekranda karşılaştıracak bir şey kalmaz."""
    body = overview(client, planner_headers, models=[])
    assert body["meta"]["models"], "bos secim bos ekran uretmemeli"


def test_actual_row_has_no_future(client, planner_headers):
    """Gerçekleşenin geleceği yoktur."""
    row = table_row(overview(client, planner_headers), "actual")
    for key, value in row["cells"].items():
        if key.endswith("_future"):
            assert value is None, f"{key} dolu gelmemeli"
        else:
            assert value > 0


def test_average_divides_the_total_by_the_period_count(client, planner_headers):
    """Toplam/ortalama bir sütun kırılımı değil, tüm sayfayı süren tek filtre."""
    total = overview(client, planner_headers, grain="quarter", aggregate="total")
    avg = overview(client, planner_headers, grain="quarter", aggregate="average")
    assert total["table"]["aggregate"] == "total"
    assert avg["table"]["aggregate"] == "average"
    # Tek deger var, toplam/ortalama diye bolunmus sutun yok.
    assert all(isinstance(v, (int, float, type(None)))
               for r in avg["table"]["rows"] for v in r["cells"].values())

    t = table_row(total, "consensus")["cells"]["units_future"]
    a = table_row(avg, "consensus")["cells"]["units_future"]
    assert a == pytest.approx(t / total["table"]["future_periods"], rel=0.01)


def test_average_also_drives_the_chart(client, planner_headers):
    """Ekranın tamamı aynı filtreye uyar: grafik de ortalamaya geçer."""
    total = overview(client, planner_headers, grain="quarter", aggregate="total")
    avg = overview(client, planner_headers, grain="quarter", aggregate="average")
    t = [v for v in model_points(total, "consensus") if v is not None]
    a = [v for v in model_points(avg, "consensus") if v is not None]
    assert sum(a) < sum(t), "grafik ortalamaya gecmemis"
    # Ceyrekte her kova uc ay tasir.
    assert sum(t) / sum(a) == pytest.approx(3.0, rel=0.1)


def test_quarter_total_equals_month_total(client, planner_headers):
    """Kova degismesi toplami degistirmez, yalnizca nasil bolundugunu degistirir."""
    m = table_row(overview(client, planner_headers), "consensus")
    q = table_row(overview(client, planner_headers, grain="quarter"), "consensus")
    assert q["cells"]["units_future"] == pytest.approx(m["cells"]["units_future"], rel=0.01)


# ------------------------------------------------ dogruluk ve butce farki
def test_accuracy_table_is_separate_from_the_measure_table(client, planner_headers):
    body = overview(client, planner_headers)
    a = body["accuracy"]
    assert [r["code"] for r in a["rows"]] == ALL_MODELS
    assert all(r["mape"] is not None for r in a["rows"])
    # Gerceklesen burada yok: kendisiyle karsilastirma olurdu.
    assert "actual" not in {r["code"] for r in a["rows"]}
    assert a["lag"] == 1 and a["budget_periods"] > 0


def test_accuracy_mape_matches_the_analysis_endpoint(client, planner_headers):
    """Tablodaki MAPE ile Analiz ekranindaki ayni sayi olmali."""
    row = accuracy_row(overview(client, planner_headers), "consensus")
    reported = client.get(
        "/api/v1/analysis/accuracy?layer=consensus&lag=1", headers=planner_headers
    ).json()["summary_metrics"]["mape"]
    assert row["mape"] == pytest.approx(reported, rel=0.02)


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


def test_every_model_gets_a_budget_gap(client, planner_headers):
    body = overview(client, planner_headers)
    for r in body["accuracy"]["rows"]:
        assert r["budget_gap"] is not None, f"{r['label']} butce farki uretmedi"


def test_budget_gap_uses_only_the_budgeted_periods(client, planner_headers):
    """Bütçe ufkun tamamını kapsamıyor; karşılaştırmayı tüm ufka yaymak modeli
    bütçenin çok üstünde gösterirdi."""
    body = overview(client, planner_headers)
    a = body["accuracy"]
    assert 0 < a["budget_periods"] <= body["table"]["future_periods"]
    # Konsensus butceye yakin seyreder; ufkun tamami kullanilsaydi yuzde 40'i
    # asardi (butce 12 donem, ufuk 18).
    assert abs(accuracy_row(body, "consensus")["budget_gap"]) < 25


def test_gap_chart_follows_its_own_model_picker(client, planner_headers):
    """Bütçe farkı tek bir modele bakar; seçenekleri üst filtredekiler."""
    body = overview(client, planner_headers, gap_model="statistical")
    chart = next(c for c in body["charts"] if c["code"] == "budget_gap")
    assert body["meta"]["gap_model"] == "statistical"
    assert chart["model"] == "statistical"
    assert chart["model_label"] == "Model 1"

    other = overview(client, planner_headers, gap_model="consensus")
    mine = [p["gap"] for p in chart["points"] if p["gap"] is not None]
    theirs = [p["gap"] for p in next(c for c in other["charts"])["points"] if p["gap"] is not None]
    assert mine != theirs, "model secimi butce farkini degistirmiyor"


def test_gap_model_must_be_among_the_selected_models(client, planner_headers):
    """Üst filtreden çıkarılan bir model bütçe farkında asılı kalamaz."""
    body = overview(client, planner_headers, models=["statistical"], gap_model="consensus")
    assert body["meta"]["gap_model"] == "statistical"


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
    a = table_row(by_count, "consensus")["cells"]["units_future"]
    b = table_row(by_range, "consensus")["cells"]["units_future"]
    assert a == pytest.approx(b, rel=0.001)


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
    assert (table_row(narrow, "statistical")["cells"]["units_future"]
            < table_row(full, "statistical")["cells"]["units_future"])
    a, b = accuracy_row(full, "statistical"), accuracy_row(narrow, "statistical")
    assert b["mape"] is not None
    assert b["mape"] != a["mape"], "MAPE kapsami takip etmiyor"


# ------------------------------------------------------------- scope, roles
def test_scope_narrows_the_series_set(client, planner_headers):
    full = overview(client, planner_headers)
    scopes = full["options"]["scopes"][0]
    node = scopes["nodes"][0]["node_id"]
    narrowed = overview(client, planner_headers, scope={scopes["dimension"]: {"nodes": [node]}})
    assert narrowed["meta"]["series_in_scope"] < full["meta"]["series_in_scope"]
    full_total = sum(v for v in model_points(full, "consensus") if v is not None)
    part_total = sum(v for v in model_points(narrowed, "consensus") if v is not None)
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
    u = sum(v for v in model_points(units, "consensus") if v is not None)
    r = sum(v for v in model_points(revenue, "consensus") if v is not None)
    assert r > u, "ciro hacimden buyuk olmali"
    assert revenue["meta"]["unit_type"] == "value"
    # Real TRY deflates by the price index, so it must be below nominal.
    real = overview(client, planner_headers, measure="revenue", view="real")
    r_real = sum(v for v in model_points(real, "consensus") if v is not None)
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
