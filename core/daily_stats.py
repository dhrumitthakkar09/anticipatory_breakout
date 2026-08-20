"""Turn a 5-minute candle stream into the two structures the scanner wants.

Firstock has no daily interval, so sessions are resampled from the 5-min feed
(the same approach ema_confluence_scanner uses). The last session present in
the data is treated as "today": that keeps the scanner runnable after hours
and on holidays, when it simply re-reports the most recent session instead of
finding nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import config
from core.candles import Candle
from core.state import StockState


@dataclass(frozen=True)
class Session:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None
    last_ts: datetime


def to_sessions(candles: list[Candle]) -> list[Session]:
    """Group 5-min candles into daily sessions, oldest first."""
    by_day: dict[date, list[Candle]] = {}
    for c in candles:
        by_day.setdefault(c.start.date(), []).append(c)

    out: list[Session] = []
    for day in sorted(by_day):
        cs = sorted(by_day[day], key=lambda c: c.start)
        vol = sum(c.volume for c in cs)
        turnover = sum(c.typical * c.volume for c in cs)
        out.append(Session(
            day=day,
            open=cs[0].open,
            high=max(c.high for c in cs),
            low=min(c.low for c in cs),
            close=cs[-1].close,
            volume=vol,
            vwap=(turnover / vol) if vol else None,
            last_ts=cs[-1].start,
        ))
    return out


def _atr_pct(completed: list[Session], period: int = config.ATR_PERIOD) -> float | None:
    """Wilder true range averaged over `period` sessions, as % of last close.

    Percent rather than points so the trend leg compares a 2% move on a ₹200
    stock with a 2% move on a ₹4000 one on the same footing.
    """
    if len(completed) < period + 1:
        return None
    trs: list[float] = []
    for prev, cur in zip(completed[-(period + 1):-1], completed[-period:]):
        trs.append(max(cur.high - cur.low,
                       abs(cur.high - prev.close),
                       abs(cur.low - prev.close)))
    last_close = completed[-1].close
    if not last_close:
        return None
    return (sum(trs) / len(trs)) / last_close * 100


def build(candles: list[Candle], oi_change_pct: float | None = None
          ) -> tuple[StockState, dict] | None:
    """-> (StockState, daily_stats) for one symbol, or None if history is thin.

    daily_stats carries `high20` and `atr_pct`, the two keys the scanner reads,
    plus context the digest prints.
    """
    sessions = to_sessions(candles)
    if len(sessions) < config.MIN_SESSIONS_REQUIRED:
        return None

    today, completed = sessions[-1], sessions[:-1]
    lookback = completed[-config.NEAR_HIGH_LOOKBACK_DAYS:]

    # The 20-day high EXCLUDES today by default: the question is how close
    # price is to taking out the PRIOR range, so today's own high must not
    # move the bar it is being measured against. Including it would also make
    # the extension filter unreachable - see config.HIGH20_INCLUDES_TODAY.
    if config.HIGH20_INCLUDES_TODAY:
        recent = completed[-(config.NEAR_HIGH_LOOKBACK_DAYS - 1):]
        high20 = max([s.high for s in recent] + [today.high])
    else:
        high20 = max(s.high for s in lookback)

    # Volume average always uses COMPLETED sessions regardless: today's volume
    # is partial, and folding it in would drag the baseline down all morning
    # and make every stock look like it is running hot.
    avg20_volume = sum(s.volume for s in lookback) / len(lookback)
    prev_close = completed[-1].close
    if not prev_close:
        return None

    open_to_close = today.last_ts.replace(hour=config.MARKET_OPEN.hour,
                                          minute=config.MARKET_OPEN.minute,
                                          second=0, microsecond=0)
    elapsed = int((today.last_ts - open_to_close).total_seconds() // 60) + config.BASE_TF_MIN
    elapsed = max(config.BASE_TF_MIN, min(elapsed, config.SESSION_MINUTES))

    state = StockState(
        ltp=today.close,
        vwap=today.vwap,
        day_change_pct=(today.close - prev_close) / prev_close * 100,
        gap_pct=(today.open - prev_close) / prev_close * 100,
        oi_change_pct=oi_change_pct,
        day_volume=today.volume,
        avg20_volume=avg20_volume,
        minutes_elapsed=elapsed,
    )
    stats = {
        "high20": high20,
        "atr_pct": _atr_pct(completed),
        "sessions": len(sessions),
        "as_of": today.day.isoformat(),
    }
    return state, stats
