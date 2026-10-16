"""Step 2 acceptance: calendar, 53 week years, working days, holiday alignment."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.domain.models import Calendar, CalendarEvent, TimeBucket
from app.services import calendar_svc, cycle_svc


def _calendar(db) -> Calendar:
    return db.execute(select(Calendar)).scalars().first()


def test_month_buckets_exist_for_every_year(db):
    cal = _calendar(db)
    months = calendar_svc.buckets_for(db, cal.id, "month")
    assert len(months) == 8 * 12  # 2022 through 2029
    assert months[0].label == "2022-01"
    assert all(m.seq == i for i, m in enumerate(months))


def test_fifty_three_week_year_is_generated(db):
    cal = _calendar(db)
    weeks = calendar_svc.buckets_for(db, cal.id, "week")
    per_year: dict[str, int] = {}
    for w in weeks:
        year = w.label.split("-W")[0]
        per_year[year] = per_year.get(year, 0) + 1
    # ISO rule: 2026 is a 53 week year.
    assert per_year.get("2026") == 53, per_year
    assert per_year.get("2025") == 52


def test_working_days_exclude_weekends_and_holidays(db):
    cal = _calendar(db)
    jan = db.execute(
        select(TimeBucket).where(
            TimeBucket.calendar_id == cal.id, TimeBucket.grain == "month", TimeBucket.label == "2026-01"
        )
    ).scalar_one()
    assert 0 < jan.working_days < 23
    events = db.execute(
        select(CalendarEvent).where(
            CalendarEvent.calendar_id == cal.id,
            CalendarEvent.start_date >= date(2026, 1, 1),
            CalendarEvent.start_date <= date(2026, 12, 31),
        )
    ).scalars().all()
    assert len(events) >= 7
    assert any(e.is_shifting for e in events), "kayan bayram yüklenmedi"


def test_holiday_aligned_prior_year_differs_when_holiday_moves(db):
    cal = _calendar(db)
    months = {b.label: b for b in calendar_svc.buckets_for(db, cal.id, "month")}
    shifted = 0
    for label, bucket in months.items():
        if not label.startswith("2026"):
            continue
        prior = calendar_svc.holiday_aligned_prior_year(db, cal.id, bucket)
        if prior is None:
            continue
        plain_seq = bucket.seq - 12
        if prior.seq != plain_seq:
            shifted += 1
    assert shifted >= 1, "hiçbir dönem bayram hizasina göre kaymadi"


def test_period_flags_order(db):
    cycle = cycle_svc.active_cycle(db)
    flags = cycle_svc.period_flags(db, cycle)
    window = cycle_svc.forecast_window(db, cycle)
    seq = [flags[b.id] for b in window]
    assert seq[0] == "fence"
    assert seq[1:1 + cycle.freeze_periods] == ["freeze"] * cycle.freeze_periods
    assert seq[-1] == "open"
    history = cycle_svc.history_window(db, cycle, 3)
    assert all(flags[b.id] == "actual" for b in history)
