"""Time backbone (section 6): calendars, buckets, Turkish calendar events."""
from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.models import Calendar, CalendarEvent, TimeBucket

GRAIN_ORDER = ["hour", "day", "week", "month", "quarter", "year"]
FLOW_GRAINS = {"day": 1, "week": 7, "month": 30, "quarter": 91, "year": 365}


def month_add(d: date, months: int) -> date:
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    return date(y, m + 1, 1)


def month_end(d: date) -> date:
    return date(d.year, d.month, _cal.monthrange(d.year, d.month)[1])


def _tr_holidays(years: list[int]) -> dict[date, str]:
    try:
        import holidays as _h

        return dict(_h.country_holidays("TR", years=years))
    except Exception:  # pragma: no cover - offline fallback
        out: dict[date, str] = {}
        for y in years:
            out[date(y, 1, 1)] = "Yılbaşı"
            out[date(y, 4, 23)] = "Ulusal Egemenlik ve Çocuk Bayramı"
            out[date(y, 5, 1)] = "Emek ve Dayanışma Günü"
            out[date(y, 5, 19)] = "Atatürk'ü Anma, Gençlik ve Spor Bayramı"
            out[date(y, 7, 15)] = "Demokrasi ve Milli Birlik Günü"
            out[date(y, 8, 30)] = "Zafer Bayramı"
            out[date(y, 10, 29)] = "Cumhuriyet Bayramı"
        return out


def working_days(start: date, end: date, holiday_map: dict[date, str]) -> int:
    n = 0
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holiday_map:
            n += 1
        d += timedelta(days=1)
    return n


def build_buckets(
    db: Session,
    cal: Calendar,
    first_year: int,
    last_year: int,
    grains: tuple[str, ...] = ("year", "quarter", "month", "week", "day"),
) -> dict[str, dict[str, TimeBucket]]:
    """Generate every bucket for the calendar. Returns {grain: {label: bucket}}."""
    db.execute(delete(TimeBucket).where(TimeBucket.calendar_id == cal.id))
    db.flush()
    years = list(range(first_year, last_year + 1))
    hmap = _tr_holidays(years)
    made: dict[str, dict[str, TimeBucket]] = {g: {} for g in grains}

    if "year" in grains:
        for i, y in enumerate(years):
            s, e = date(y, 1, 1), date(y, 12, 31)
            b = TimeBucket(
                calendar_id=cal.id, grain="year", start_date=s, end_date=e, label=str(y), seq=i,
                working_days=working_days(s, e, hmap),
            )
            db.add(b)
            made["year"][b.label] = b
        db.flush()

    if "quarter" in grains:
        i = 0
        for y in years:
            for q in range(1, 5):
                s = date(y, 3 * (q - 1) + 1, 1)
                e = month_end(month_add(s, 2))
                label = f"{y}-Q{q}"
                parent = made.get("year", {}).get(str(y))
                b = TimeBucket(
                    calendar_id=cal.id, grain="quarter", start_date=s, end_date=e, label=label, seq=i,
                    parent_bucket_id=parent.id if parent else None,
                    working_days=working_days(s, e, hmap),
                )
                db.add(b)
                made["quarter"][label] = b
                i += 1
        db.flush()

    if "month" in grains:
        i = 0
        for y in years:
            for m in range(1, 13):
                s = date(y, m, 1)
                e = month_end(s)
                label = f"{y}-{m:02d}"
                parent = made.get("quarter", {}).get(f"{y}-Q{(m - 1) // 3 + 1}")
                b = TimeBucket(
                    calendar_id=cal.id, grain="month", start_date=s, end_date=e, label=label, seq=i,
                    parent_bucket_id=parent.id if parent else None,
                    working_days=working_days(s, e, hmap),
                )
                db.add(b)
                made["month"][label] = b
                i += 1
        db.flush()

    if "week" in grains:
        # ISO weeks. A 53 week year is produced automatically by the ISO rule.
        d = date(first_year, 1, 1)
        d -= timedelta(days=d.weekday())
        i = 0
        while d <= date(last_year, 12, 31):
            e = d + timedelta(days=6)
            iso = d.isocalendar()
            label = f"{iso.year}-W{iso.week:02d}"
            b = TimeBucket(
                calendar_id=cal.id, grain="week", start_date=d, end_date=e, label=label, seq=i,
                working_days=working_days(d, e, hmap),
            )
            db.add(b)
            made["week"][label] = b
            d += timedelta(days=7)
            i += 1
        db.flush()

    if "day" in grains:
        d = date(first_year, 1, 1)
        i = 0
        while d <= date(last_year, 12, 31):
            label = d.isoformat()
            parent = made.get("month", {}).get(f"{d.year}-{d.month:02d}")
            b = TimeBucket(
                calendar_id=cal.id, grain="day", start_date=d, end_date=d, label=label, seq=i,
                parent_bucket_id=parent.id if parent else None,
                working_days=1 if (d.weekday() < 5 and d not in hmap) else 0,
            )
            db.add(b)
            made["day"][label] = b
            d += timedelta(days=1)
            i += 1
        db.flush()

    return made


def load_calendar_events(db: Session, cal: Calendar, first_year: int, last_year: int) -> int:
    """Official holidays, bridge days and shifting religious holidays."""
    db.execute(delete(CalendarEvent).where(CalendarEvent.calendar_id == cal.id))
    years = list(range(first_year, last_year + 1))
    hmap = _tr_holidays(years)
    count = 0
    for d, name in sorted(hmap.items()):
        lower = name.lower()
        shifting = any(k in lower for k in ("ramaz", "kurban", "eid", "şeker", "seker", "sacrifice"))
        etype = "ramadan" if ("ramaz" in lower or "şeker" in lower or "seker" in lower) else (
            "kurban" if ("kurban" in lower or "sacrifice" in lower) else "holiday"
        )
        db.add(
            CalendarEvent(
                calendar_id=cal.id, type=etype, start_date=d, end_date=d, name=name, is_shifting=shifting
            )
        )
        count += 1
        # Bridge day: a single working day between a holiday and the weekend.
        nxt = d + timedelta(days=1)
        if d.weekday() == 3 and nxt not in hmap:
            db.add(
                CalendarEvent(
                    calendar_id=cal.id, type="bridge", start_date=nxt, end_date=nxt,
                    name=f"{name} köprüsü", is_shifting=shifting,
                )
            )
            count += 1
    db.flush()
    return count


def buckets_for(db: Session, calendar_id: int, grain: str) -> list[TimeBucket]:
    return list(
        db.execute(
            select(TimeBucket)
            .where(TimeBucket.calendar_id == calendar_id, TimeBucket.grain == grain)
            .order_by(TimeBucket.seq)
        ).scalars()
    )


def bucket_window(db: Session, calendar_id: int, grain: str, seq_from: int, seq_to: int) -> list[TimeBucket]:
    return list(
        db.execute(
            select(TimeBucket)
            .where(
                TimeBucket.calendar_id == calendar_id,
                TimeBucket.grain == grain,
                TimeBucket.seq >= seq_from,
                TimeBucket.seq <= seq_to,
            )
            .order_by(TimeBucket.seq)
        ).scalars()
    )


def holiday_aligned_prior_year(db: Session, cal_id: int, bucket: TimeBucket) -> TimeBucket | None:
    """Prior year bucket shifted to the same religious holiday position.

    Calendar aligned prior year is bucket.seq - 12 for months. When a shifting
    holiday falls in the bucket, the prior year bucket that contains the same
    holiday is used instead.
    """
    events = list(
        db.execute(
            select(CalendarEvent).where(
                CalendarEvent.calendar_id == cal_id,
                CalendarEvent.is_shifting.is_(True),
                CalendarEvent.start_date >= bucket.start_date,
                CalendarEvent.start_date <= bucket.end_date,
            )
        ).scalars()
    )
    plain = db.execute(
        select(TimeBucket).where(
            TimeBucket.calendar_id == cal_id, TimeBucket.grain == bucket.grain, TimeBucket.seq == bucket.seq - 12
        )
    ).scalar_one_or_none()
    if not events:
        return plain
    target = events[0]
    prior = list(
        db.execute(
            select(CalendarEvent).where(
                CalendarEvent.calendar_id == cal_id,
                CalendarEvent.type == target.type,
                CalendarEvent.start_date >= date(target.start_date.year - 1, 1, 1),
                CalendarEvent.start_date <= date(target.start_date.year - 1, 12, 31),
            )
        ).scalars()
    )
    if not prior:
        return plain
    pd = prior[0].start_date
    return db.execute(
        select(TimeBucket).where(
            TimeBucket.calendar_id == cal_id,
            TimeBucket.grain == bucket.grain,
            TimeBucket.start_date <= pd,
            TimeBucket.end_date >= pd,
        )
    ).scalar_one_or_none() or plain
