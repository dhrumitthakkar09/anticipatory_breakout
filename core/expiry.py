"""Expiry-cycle trust factor for the OI leg.

OI is only evidence of conviction when the series it is measured on is mature.
Two windows where it is not:

  * just after rollover - the new near-month future starts near-empty, so a
    +40% OI day is a handful of lots, not accumulation;
  * the last day or two before expiry - positions are being closed or rolled,
    so falling OI says nothing about the underlying's direction.

NSE moved F&O expiries to Tuesday during 2025; stock futures are monthly, so
the cycle boundary is the last Tuesday of the month (last Thursday before the
switchover date, which only matters if this is ever run over old data).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import config

TUESDAY_EXPIRY_FROM = date(2025, 9, 1)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """weekday: Monday=0 ... Sunday=6."""
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def monthly_expiry(year: int, month: int) -> date:
    weekday = 1 if date(year, month, 1) >= TUESDAY_EXPIRY_FROM else 3  # Tue : Thu
    return _last_weekday_of_month(year, month, weekday)


def current_cycle(today: date) -> tuple[date, date]:
    """-> (previous expiry, next expiry) bracketing `today`."""
    this_month = monthly_expiry(today.year, today.month)
    if today <= this_month:
        prev_month = (today.replace(day=1) - timedelta(days=1))
        return monthly_expiry(prev_month.year, prev_month.month), this_month
    nxt = (today.replace(day=28) + timedelta(days=7)).replace(day=1)
    return this_month, monthly_expiry(nxt.year, nxt.month)


def _weekdays_between(a: date, b: date) -> int:
    """Trading-session approximation - weekday count, holidays ignored."""
    days = 0
    cur = a + timedelta(days=1)
    while cur <= b:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days


def expiry_info(today: date | None = None) -> dict:
    """-> {'oi_trust': 0..1, ...} for the scanner's `expiry_info` argument."""
    today = today or date.today()
    prev_exp, next_exp = current_cycle(today)
    since_roll = _weekdays_between(prev_exp, today)
    to_expiry = _weekdays_between(today, next_exp)

    if to_expiry <= config.OI_EXPIRY_TAIL_DAYS:
        trust, why = config.OI_TRUST_EXPIRY_TAIL, "expiry week unwinding"
    elif since_roll < config.OI_TRUST_RAMP_DAYS:
        span = config.OI_TRUST_RAMP_DAYS
        trust = config.OI_TRUST_MIN + (1.0 - config.OI_TRUST_MIN) * (since_roll / span)
        why = f"new series, {since_roll}/{span} sessions in"
    else:
        trust, why = 1.0, "mid-cycle"

    return {
        "oi_trust": round(trust, 3),
        "prev_expiry": prev_exp,
        "next_expiry": next_exp,
        "sessions_since_rollover": since_roll,
        "sessions_to_expiry": to_expiry,
        "reason": why,
    }
