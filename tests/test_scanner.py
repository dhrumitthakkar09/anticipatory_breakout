"""Scoring, tiering and filter tests - the parts that decide what you see."""
from __future__ import annotations

from datetime import date

import config
from core.expiry import expiry_info, monthly_expiry
from core.scanner import AnticipatoryBreakout
from core.state import StockState


def _state(**kw) -> StockState:
    base = dict(ltp=100.0, vwap=99.0, day_change_pct=1.0, gap_pct=0.2,
                oi_change_pct=2.0, day_volume=1_000_000, avg20_volume=1_000_000,
                minutes_elapsed=config.SESSION_MINUTES)
    base.update(kw)
    return StockState(**base)


def _scan(states, stats, **kw):
    return AnticipatoryBreakout().scan(states, stats, **kw)


# --------------------------------------------------------------- scoring
def test_perfect_setup_scores_100():
    """Every leg maxed -> 100, and the top tier."""
    st = _state(ltp=100.0, vwap=95.0, day_change_pct=5.0, oi_change_pct=10.0,
                day_volume=2_000_000, avg20_volume=1_000_000)
    res = _scan({"X": st}, {"X": {"high20": 100.0, "atr_pct": 1.0}})
    assert res["super_excellent"][0]["score"] == 100


def test_distance_filter_excludes_far_names():
    """Past MAX_DISTANCE_PCT below the high, a stock is not scored at all -
    but it still counts toward total_scanned and breadth."""
    st = _state(ltp=100.0)
    res = _scan({"X": st}, {"X": {"high20": 120.0, "atr_pct": 1.0}})
    assert res["total_scanned"] == 1
    assert not (res["super_excellent"] + res["excellent"] + res["early_watch"])


def test_extension_filter_allows_small_break_but_not_a_runaway():
    """0.5% past the high is still tradable; 5% past it has already gone."""
    stats = {"X": {"high20": 100.0, "atr_pct": 1.0}}
    near = _scan({"X": _state(ltp=100.5)}, stats)
    far = _scan({"X": _state(ltp=105.0)}, stats)
    assert near["super_excellent"] or near["excellent"] or near["early_watch"]
    assert not (far["super_excellent"] + far["excellent"] + far["early_watch"])


def test_unknown_oi_scores_the_constant_not_zero():
    known = _scan({"X": _state(oi_change_pct=None)},
                  {"X": {"high20": 100.0, "atr_pct": 1.0}})
    zero = _scan({"X": _state(oi_change_pct=0.0)},
                 {"X": {"high20": 100.0, "atr_pct": 1.0}})
    rows = known["super_excellent"] + known["excellent"] + known["early_watch"]
    assert rows[0]["parts"]["oi"] == config.OI_UNKNOWN_CREDIT
    zrows = zero["super_excellent"] + zero["excellent"] + zero["early_watch"]
    assert zrows[0]["parts"]["oi"] == 0.0
    assert rows[0]["score"] > zrows[0]["score"]


def test_oi_trust_discounts_the_oi_leg():
    stats = {"X": {"high20": 100.0, "atr_pct": 1.0}}
    full = _scan({"X": _state()}, stats, expiry_info={"oi_trust": 1.0})
    half = _scan({"X": _state()}, stats, expiry_info={"oi_trust": 0.5})
    f = (full["super_excellent"] + full["excellent"] + full["early_watch"])[0]
    h = (half["super_excellent"] + half["excellent"] + half["early_watch"])[0]
    assert h["parts"]["oi"] == f["parts"]["oi"] / 2
    assert h["score"] < f["score"]


def test_trend_is_measured_in_the_stocks_own_atr():
    """A 2% day is a full trend leg for a calm stock and only partial for a
    volatile one - the whole point of dividing by ATR."""
    calm = _scan({"X": _state(day_change_pct=2.0)},
                 {"X": {"high20": 100.0, "atr_pct": 1.0}})
    wild = _scan({"X": _state(day_change_pct=2.0)},
                 {"X": {"high20": 100.0, "atr_pct": 8.0}})
    c = (calm["super_excellent"] + calm["excellent"] + calm["early_watch"])[0]
    w = (wild["super_excellent"] + wild["excellent"] + wild["early_watch"])[0]
    assert c["parts"]["trend"] == 1.0
    assert w["parts"]["trend"] < 0.5


# --------------------------------------------------------------- breadth
def test_saturation_flag_trips_when_too_many_qualify():
    """If most of the universe qualifies, the scan is describing the market,
    not selecting from it."""
    states = {f"S{i}": _state() for i in range(10)}
    stats = {f"S{i}": {"high20": 100.0, "atr_pct": 1.0} for i in range(10)}
    res = _scan(states, stats)
    assert res["qualify_pct"] > config.SATURATION_PCT
    assert res["discriminating"] is False


def test_ultra_safe_excludes_gapped_names():
    """Same score, different gap: only the tight one is ultra-safe."""
    perfect = dict(ltp=100.0, vwap=95.0, day_change_pct=5.0, oi_change_pct=10.0,
                   day_volume=2_000_000, avg20_volume=1_000_000)
    states = {"TIGHT": _state(gap_pct=0.3, **perfect),
              "GAPPED": _state(gap_pct=4.0, **perfect)}
    stats = {s: {"high20": 100.0, "atr_pct": 1.0} for s in states}
    res = _scan(states, stats)
    assert len(res["super_excellent"]) == 2
    assert res["ultra_safe"] == 1


def test_breadth_counts_every_symbol_with_a_price():
    states = {"UP": _state(day_change_pct=1.0), "DOWN": _state(day_change_pct=-1.0),
              "DEAD": _state(ltp=0.0)}
    stats = {s: {"high20": 100.0, "atr_pct": 1.0} for s in states}
    res = _scan(states, stats)
    assert res["total_scanned"] == 2          # the zero-LTP name is skipped entirely
    assert res["breadth_up_pct"] == 50.0


# --------------------------------------------------------------- volume pace
def test_volume_pace_prorates_by_elapsed_session():
    """Half the average volume at half past the session = exactly on pace."""
    st = _state(day_volume=500_000, avg20_volume=1_000_000,
                minutes_elapsed=config.SESSION_MINUTES // 2)
    assert abs(st.volume_pace() - 1.0) < 0.01


def test_volume_pace_is_none_without_an_average():
    assert _state(avg20_volume=0).volume_pace() is None


# --------------------------------------------------------------- expiry
def test_monthly_expiry_is_the_last_tuesday_post_switchover():
    assert monthly_expiry(2026, 8) == date(2026, 8, 25)   # last Tue of Aug 2026
    assert monthly_expiry(2025, 3) == date(2025, 3, 27)   # last Thu, pre-switchover


def test_oi_trust_is_reduced_right_after_rollover():
    prev_exp = monthly_expiry(2026, 8)                    # 25-Aug-2026 (Tue)
    fresh = expiry_info(prev_exp + __import__("datetime").timedelta(days=1))
    assert fresh["oi_trust"] < 1.0
