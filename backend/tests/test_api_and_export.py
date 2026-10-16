"""API surface, authorisation, grid performance and the Excel round trip."""
from __future__ import annotations

import io
import time

from openpyxl import load_workbook
from sqlalchemy import select

from app.domain.models import ReasonCode


def test_every_endpoint_requires_a_session(client):
    for method, path in [
        ("get", "/api/v1/dimensions"),
        ("get", "/api/v1/layers"),
        ("post", "/api/v1/grid/query"),
        ("get", "/api/v1/analysis/accuracy"),
        ("get", "/api/v1/admin/audit"),
        ("post", "/api/v1/override"),
    ]:
        res = client.post(path, json={}) if method == "post" else client.get(path)
        assert res.status_code == 401, f"{path} oturumsuz erisime izin verdi"


def test_openapi_documents_every_route(client):
    spec = client.get("/api/openapi.json").json()
    paths = spec["paths"]
    assert len(paths) > 35
    for path, ops in paths.items():
        for method, op in ops.items():
            if method in ("get", "post", "delete", "put"):
                assert op.get("summary"), f"{method} {path} ozetsiz"


def test_admin_only_routes_reject_planner(client, planner_headers):
    assert client.get("/api/v1/admin/connections", headers=planner_headers).status_code == 403
    assert client.get("/api/v1/admin/users", headers=planner_headers).status_code == 403
    assert client.post("/api/v1/admin/models/sync", headers=planner_headers).status_code == 403


def test_grid_performance_target(client, planner_headers):
    """1000 series by 24 periods must come back under two seconds."""
    t0 = time.perf_counter()
    res = client.post(
        "/api/v1/grid/query", headers=planner_headers,
        json={
            "version_id": 1, "level": {"product": "sku", "geo": "city"}, "grain": "month",
            "layers": ["actual", "statistical", "sales", "planner", "consensus", "budget"],
            "history_periods": 6,
        },
    )
    elapsed = time.perf_counter() - t0
    assert res.status_code == 200, res.text
    body = res.json()
    cells = len(body["rows"]) * len(body["columns"])
    assert cells >= 200 * 24
    assert elapsed < 2.0, f"grid {elapsed:.2f} saniye surdu"


def test_grid_marks_period_state_and_cell_flags(client, planner_headers):
    body = client.post(
        "/api/v1/grid/query", headers=planner_headers,
        json={"version_id": 1, "level": {"product": "category"}, "layers": ["statistical", "sales", "consensus"]},
    ).json()
    flags = {c["flag"] for c in body["columns"]}
    assert {"actual", "fence", "freeze", "open"} <= flags
    assert sum(1 for c in body["columns"] if c["is_current"]) == 1
    assert any("consensus_from" in f for row in body["rows"] for f in row["flags"].values())
    assert body["total"]["layers"]["consensus"]


def test_grain_conversion_through_the_api(client, planner_headers):
    monthly = client.post(
        "/api/v1/grid/query", headers=planner_headers,
        json={"version_id": 1, "level": {"product": "category"}, "grain": "month",
              "layers": ["statistical"], "history_periods": 0},
    ).json()
    quarterly = client.post(
        "/api/v1/grid/query", headers=planner_headers,
        json={"version_id": 1, "level": {"product": "category"}, "grain": "quarter",
              "layers": ["statistical"], "history_periods": 0},
    ).json()
    m_total = sum(sum(r["layers"]["statistical"].values()) for r in monthly["rows"])
    q_total = sum(sum(r["layers"]["statistical"].values()) for r in quarterly["rows"])
    assert abs(m_total - q_total) / m_total < 0.01
    assert len(quarterly["columns"]) < len(monthly["columns"])


def test_explain_cell_returns_the_chain(client, planner_headers):
    grid = client.post(
        "/api/v1/grid/query", headers=planner_headers,
        json={"version_id": 1, "level": {"product": "category"}, "layers": ["consensus"]},
    ).json()
    row = grid["rows"][0]
    bucket = next(c for c in grid["columns"] if c["flag"] == "open")
    body = client.post(
        "/api/v1/grid/explain", headers=planner_headers,
        json={"version_id": 1, "layer": "consensus", "group": row["group"],
              "bucket_id": bucket["bucket_id"], "level": {"product": "category"}},
    ).json()
    assert body["version"]["provenance"]["model"]
    assert body["consensus_rule"]
    assert body["layer_values"]
    assert "rule_effects" in body


def test_rule_effects_are_separable_for_a_run_with_rules(client, planner_headers):
    runs = client.post("/api/v1/runs", headers=planner_headers, json={"scenario_ids": [3]}).json()
    run_id = runs["runs"][0]["run_id"]
    for _ in range(120):
        body = client.get(f"/api/v1/runs/{run_id}", headers=planner_headers).json()
        if body["status"] == "done":
            break
        if body["status"] == "failed":
            raise AssertionError(body.get("error"))
        time.sleep(0.25)
    version_id = body["version"]["id"]
    assert body["diagnostics"]["rules_applied"] == 3
    assert body["diagnostics"]["rule_effect_rows"] > 0

    compare = client.get(
        f"/api/v1/versions/compare?ids=1,{version_id}", headers=planner_headers
    ).json()
    target = next(c for c in compare["columns"] if c["version_id"] == version_id)
    assert len(target["rule_effects"]) == 3
    names = {e["name"] for e in target["rule_effects"]}
    assert "Atıştırmalık kampanyası" in names
    assert abs(target["delta_vs_base"]) > 0


def test_excel_round_trip_produces_diff_and_applies(client, planner_headers, db):
    payload = {
        "version_id": 1, "level": {"product": "category"},
        "layers": ["statistical", "planner", "consensus"], "history_periods": 0,
    }
    export = client.post("/api/v1/export/grid", headers=planner_headers, json=payload)
    assert export.status_code == 200
    wb = load_workbook(io.BytesIO(export.content))
    assert wb["Tahmin"].protection.sheet is True
    assert "Meta" in wb.sheetnames

    ws = wb["Tahmin"]
    # Only the planner layer is editable for this user, and only outside the fence.
    changed = 0
    for row in range(2, ws.max_row + 1):
        if ws.cell(row=row, column=2).value != "Planlamacı":
            continue
        for col in range(3, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            if cell.protection and cell.protection.locked is False and cell.value:
                cell.value = round(float(cell.value) * 1.1, 1)
                changed += 1
                break
        if changed:
            break
    assert changed == 1, "düzenlenebilir hücre bulunamadi"

    buf = io.BytesIO()
    wb.save(buf)
    preview = client.post(
        "/api/v1/import/grid/preview", headers=planner_headers,
        files={"file": ("grid.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    ).json()
    assert preview["count"] == 1, preview
    assert preview["diffs"][0]["layer"] == "planner"

    reason = db.execute(select(ReasonCode).where(ReasonCode.code == "promo")).scalar_one()
    applied = client.post(
        "/api/v1/import/grid/apply", headers=planner_headers,
        json={"payload": preview, "reason_code_id": reason.id, "note": "Excel testi"},
    ).json()
    assert applied["applied"] == 1, applied


def test_sector_swap_needs_no_code_change(tmp_path):
    """configs/sectors/pharma.yaml loads with the same loader and no code edit."""
    from app.core.config import settings
    from app.services.sector_loader import read_yaml

    pharma = read_yaml(settings.repo_root / "configs" / "sectors" / "pharma.yaml")
    demo = read_yaml(settings.sector_config_path)
    assert pharma["sector"] == "pharma"
    assert {d["code"] for d in pharma["dimensions"]} == {"product", "channel"}
    assert set(demo) == set(pharma), "iki sektör ayni semayi kullanmali"
    assert "tender" in {r["code"] for r in pharma["reason_codes"]}


def test_assistant_offers_only_permitted_tools(client, sales_headers, admin_headers):
    sales = client.get("/api/v1/assistant/tools", headers=sales_headers).json()
    admin = client.get("/api/v1/assistant/tools", headers=admin_headers).json()
    sales_names = {t["name"] for g in sales["groups"] for t in g["tools"]}
    admin_names = {t["name"] for g in admin["groups"] for t in g["tools"]}
    assert "list_connections" not in sales_names
    assert "list_connections" in admin_names
    assert "override" in sales_names
    assert sales["behaviour_rules"]


def test_assistant_write_tools_need_confirmation(client, planner_headers):
    body = client.post(
        "/api/v1/assistant/chat", headers=planner_headers,
        json={"message": "Izmir haziran ayini yüzde 10 artir"},
    ).json()
    assert body["ok"] is False
    assert body["status"] == "not_connected"
    prompt = client.get("/api/v1/assistant/system-prompt", headers=planner_headers).json()
    assert "onay" in prompt["system_prompt"]
    assert "sayı uretmezsin" in prompt["system_prompt"]


def test_public_forecast_api_filters_by_scope(client, planner_headers):
    body = client.get(
        "/api/v1/forecast?version=1&layer=consensus&scope=geo%3DIST", headers=planner_headers
    ).json()
    assert body["rows"]
    assert all(r["geo"] == "IST" for r in body["rows"])


def test_every_response_has_a_human_summary(client, planner_headers):
    for path in ["/api/v1/layers", "/api/v1/scenarios", "/api/v1/versions",
                 "/api/v1/analysis/fva", "/api/v1/reports", "/api/v1/time/cycle"]:
        body = client.get(path, headers=planner_headers).json()
        assert body.get("summary"), f"{path} özet alanı taşımıyor"
    cockpit = client.post("/api/v1/cockpit/overview", headers=planner_headers, json={}).json()
    assert cockpit.get("summary"), "kokpit özet alanı taşımıyor"
