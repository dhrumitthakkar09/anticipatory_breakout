"""Offline demo - synthetic candles through the real pipeline.

Exercises daily_stats -> scanner -> console formatting with no Firstock
credentials and no network, so a fresh clone can be verified end to end
before any API access is arranged. The numbers are made up; the code path is
the same one the scheduled digest uses.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import config
from core.candles import Candle
from core.daily_stats import build
from core.expiry import expiry_info
from core.scanner import AnticipatoryBreakout

BARS_PER_SESSION = config.SESSION_MINUTES // config.BASE_TF_MIN   # 75


def _session_candles(day: datetime, o: float, c: float, hi: float, lo: float,
                     volume: float) -> list[Candle]:
    """Split one session into 5-min bars walking linearly from open to close."""
    out = []
    step = (c - o) / max(BARS_PER_SESSION - 1, 1)
    per_bar_vol = volume / BARS_PER_SESSION
    for i in range(BARS_PER_SESSION):
        start = day + timedelta(minutes=config.BASE_TF_MIN * i)
        bar_o = o + step * i
        bar_c = o + step * (i + 1) if i < BARS_PER_SESSION - 1 else c
        bar_hi = max(bar_o, bar_c) * 1.001
        bar_lo = min(bar_o, bar_c) * 0.999
        if i == BARS_PER_SESSION // 2:          # park the session extremes midday
            bar_hi, bar_lo = hi, lo
        out.append(Candle(start, bar_o, bar_hi, bar_lo, bar_c, per_bar_vol))
    return out


def _sessions(base: float, high_above_pct: float, recent_reach: float,
              day_range_pct: float, avg_volume: float, today_chg_pct: float,
              today_gap_pct: float, today_vol_mult: float,
              sessions: int = 30) -> list[Candle]:
    """Completed sessions oscillating under a 20-day high, then today.

    `recent_reach` (0..1) is how close the recent sessions sit to the one peak
    session - near 1.0 the stock is coiled right under its high, near 0.1 it
    peaked weeks ago and has drifted well below.
    """
    candles: list[Candle] = []
    day = datetime.now().replace(hour=9, minute=15, second=0, microsecond=0) \
        - timedelta(days=sessions + 12)
    peak_at = sessions - 8                      # the 20-day high sits mid-window

    for i in range(sessions):
        while day.weekday() >= 5:               # weekdays only
            day += timedelta(days=1)
        reach = 1.0 if i == peak_at else recent_reach * (0.85 + 0.15 * ((i % 5) / 4))
        hi = base * (1 + high_above_pct / 100 * reach)
        lo = hi * (1 - day_range_pct / 100)
        o, c = lo + (hi - lo) * 0.4, lo + (hi - lo) * 0.6
        candles += _session_candles(day, o, c, hi, lo, avg_volume)
        day += timedelta(days=1)

    while day.weekday() >= 5:
        day += timedelta(days=1)
    prev_close = candles[-1].close
    t_open = prev_close * (1 + today_gap_pct / 100)
    t_close = prev_close * (1 + today_chg_pct / 100)
    candles += _session_candles(
        day, t_open, t_close,
        max(t_open, t_close) * 1.002, min(t_open, t_close) * 0.998,
        avg_volume * today_vol_mult)
    return candles


# symbol -> (base, high above base %, recent reach, daily range %, avg vol,
#            today chg %, today gap %, today volume multiple)
# Each row is chosen to exercise a different branch of the scoring.
SPECS = {
    "RELIANCE":   (1400,  4.0, 0.92, 1.6,  9_000_000,  1.5,  0.4, 1.55),
    "TATAMOTORS": (980,   6.0, 0.80, 2.2,  6_800_000,  3.0,  1.9, 1.70),
    "INFY":       (1560,  5.5, 0.90, 1.4,  5_200_000,  1.0,  0.2, 1.10),
    "HDFCBANK":   (1700,  3.0, 0.90, 1.1,  7_500_000, -0.8, -0.5, 0.60),
    "ITC":        (410,   2.5, 0.90, 1.0, 11_000_000,  0.4,  0.1, 0.95),
    "ZOMATO":     (240,  15.0, 0.15, 2.8, 18_000_000,  1.1,  0.3, 1.20),
}
# What each row is there to show:
#   RELIANCE    coiled right under the high, hot volume, OI building -> top tier
#   TATAMOTORS  same quality but 1.9% gapped -> top tier, NOT ultra-safe
#   INFY        mid-range with unknown OI -> the 0.4 constant leg in action
#   HDFCBANK    red and below VWAP -> scores out entirely
#   ITC         flat and on-pace -> EARLY WATCH at best
#   ZOMATO      peaked weeks ago -> filtered by MAX_DISTANCE_PCT before scoring
# OI is faked here; live runs read it from the nearest monthly future.
DEMO_OI = {"RELIANCE": 3.1, "TATAMOTORS": 2.4, "INFY": None,
           "HDFCBANK": -1.2, "ITC": 0.6, "ZOMATO": 1.0}


def run() -> dict:
    states, stats = {}, {}
    for sym, spec in SPECS.items():
        built = build(_sessions(*spec), oi_change_pct=DEMO_OI[sym])
        if built is None:
            continue
        states[sym], stats[sym] = built
    return AnticipatoryBreakout().scan(
        states, stats, regime="DEMO", expiry_info=expiry_info())
