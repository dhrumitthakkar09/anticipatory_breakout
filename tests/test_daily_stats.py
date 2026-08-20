"""Session resampling and the 20-day-high definition."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import config
from core.candles import Candle
from core.daily_stats import build, to_sessions

BARS = config.SESSION_MINUTES // config.BASE_TF_MIN


def _session(day: datetime, o, h, l, c, vol=100_000.0) -> list[Candle]:
    """One session whose aggregate OHLCV is exactly (o, h, l, c, vol)."""
    per = vol / BARS
    out = [Candle(day, o, max(o, h), min(o, l), o, per)]
    out += [Candle(day + timedelta(minutes=5 * i), o, h, l, c, per)
            for i in range(1, BARS - 1)]
    out.append(Candle(day + timedelta(minutes=5 * (BARS - 1)), c, max(c, h), min(c, l), c, per))
    return out


def _history(n_sessions: int, high: float, today_high: float,
             today_close: float) -> list[Candle]:
    """n-1 flat completed sessions peaking at `high`, then today."""
    candles: list[Candle] = []
    day = datetime(2026, 6, 1, 9, 15)
    for i in range(n_sessions - 1):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        h = high if i == n_sessions - 5 else high * 0.97
        candles += _session(day, 100.0, h, 99.0, 100.0)
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    candles += _session(day, 100.0, today_high, 99.0, today_close)
    return candles


def test_high20_excludes_today_by_default(monkeypatch):
    """A new high made TODAY must not raise the bar today is measured against."""
    monkeypatch.setattr(config, "HIGH20_INCLUDES_TODAY", False)
    _, ds = build(_history(25, high=110.0, today_high=120.0, today_close=115.0))
    assert ds["high20"] == 110.0


def test_high20_can_include_today_when_configured(monkeypatch):
    monkeypatch.setattr(config, "HIGH20_INCLUDES_TODAY", True)
    _, ds = build(_history(25, high=110.0, today_high=120.0, today_close=115.0))
    assert ds["high20"] == 120.0


def test_including_today_makes_the_extension_filter_unreachable(monkeypatch):
    """The reason the default is False: a session high is always >= its close,
    so with today included the distance can never go negative and the
    'up to 1% past the high' rule can never fire."""
    monkeypatch.setattr(config, "HIGH20_INCLUDES_TODAY", True)
    st, ds = build(_history(25, high=110.0, today_high=120.0, today_close=115.0))
    assert (ds["high20"] - st.ltp) >= 0

    monkeypatch.setattr(config, "HIGH20_INCLUDES_TODAY", False)
    st, ds = build(_history(25, high=110.0, today_high=120.0, today_close=115.0))
    assert (ds["high20"] - st.ltp) < 0          # genuinely extended past the high


def test_volume_average_ignores_todays_partial_volume(monkeypatch):
    """Today's volume is incomplete; folding it into the baseline would make
    every stock look like it is running hot all morning."""
    monkeypatch.setattr(config, "HIGH20_INCLUDES_TODAY", True)
    st, _ = build(_history(25, high=110.0, today_high=111.0, today_close=110.0))
    assert st.avg20_volume == pytest.approx(100_000.0)


def test_thin_history_is_rejected():
    assert build(_history(5, high=110.0, today_high=111.0, today_close=110.0)) is None


def test_sessions_aggregate_five_minute_bars():
    cs = _session(datetime(2026, 6, 1, 9, 15), 100.0, 108.0, 95.0, 104.0, vol=75_000.0)
    s = to_sessions(cs)[0]
    assert (s.open, s.high, s.low, s.close) == (100.0, 108.0, 95.0, 104.0)
    assert s.volume == pytest.approx(75_000.0)
    assert 95.0 <= s.vwap <= 108.0
