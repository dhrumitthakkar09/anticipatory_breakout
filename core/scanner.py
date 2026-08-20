"""Anticipatory Breakout Detector - the scoring engine.

Scoring is unchanged from the deployed version: same five legs, same weights,
same thresholds, same OI handling and breadth check. Only the tier names
differ (JACKPOT -> SUPER EXCELLENT, STRONG WATCH -> EXCELLENT).

Score is 0-100, a weighted mean of five legs each normalised to 0..1:

  proximity  1.0 at the 20-day high, falling linearly to 0 at MAX_DISTANCE_PCT
  vwap       binary - above the session VWAP or not
  trend      day change / (own ATR% x TREND_ATR_FRAC), capped at 1
  oi         OI change / OI_FULL_CREDIT_PCT, capped at 1, x expiry trust
  volume     volume pace ramped from 0.8x (0) to 1.5x (1)

The engine is pure: it takes states and daily_stats and returns a dict. All
I/O lives in feed/, alerts.py and runner/.
"""
from __future__ import annotations

from datetime import datetime

import config


def _cfg() -> dict:
    return {
        "max_distance_pct": config.MAX_DISTANCE_PCT,
        "max_extension_pct": config.MAX_EXTENSION_PCT,
        "early_min_score": config.EARLY_MIN_SCORE,
        "strong_min_score": config.STRONG_MIN_SCORE,
        "jackpot_min_score": config.JACKPOT_MIN_SCORE,
        "saturation_pct": config.SATURATION_PCT,
        "trend_atr_frac": config.TREND_ATR_FRAC,
        "oi_full_credit_pct": config.OI_FULL_CREDIT_PCT,
        "oi_unknown_credit": config.OI_UNKNOWN_CREDIT,
        "ultra_safe_max_gap_pct": config.ULTRA_SAFE_MAX_GAP_PCT,
        "weights": dict(config.WEIGHTS),
    }


class AnticipatoryBreakout:
    name = "Anticipatory Breakout Detector"
    code = "ANTIC_BO"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or _cfg()

    def scan(self, states: dict, daily_stats: dict,
             regime=None, expiry_info: dict | None = None) -> dict:
        c = self.cfg
        w = c["weights"]
        oi_trust = (expiry_info or {}).get("oi_trust", 1.0)

        rows, advancing, total = [], 0, 0
        for sym, st in states.items():
            ds = daily_stats.get(sym)
            if not st.ltp:
                continue
            total += 1
            if st.day_change_pct > 0:
                advancing += 1
            if not ds or not ds.get("high20"):
                continue

            # skip names too far from (or already too far past) the 20d high
            dist_pct = (ds["high20"] - st.ltp) / st.ltp * 100
            if dist_pct < -c["max_extension_pct"] or dist_pct > c["max_distance_pct"]:
                continue

            parts = {}

            # proximity - continuous, weighted heaviest: 0.4% away is not the
            # same fact as 9.8% away, and counting both as "near" loses the
            # single most informative number in the scan
            parts["proximity"] = max(0.0, 1.0 - max(dist_pct, 0.0) / c["max_distance_pct"])

            parts["vwap"] = 1.0 if (st.vwap and st.ltp > st.vwap) else 0.0

            # trend, measured in the stock's own ATR units
            atr = ds.get("atr_pct")
            chg = st.day_change_pct
            if chg <= 0:
                parts["trend"] = 0.0
            elif atr:
                parts["trend"] = min(1.0, chg / (atr * c["trend_atr_frac"]))
            else:
                parts["trend"] = min(1.0, chg / 1.5)

            oi = st.oi_change_pct
            if oi is None:
                parts["oi"] = c["oi_unknown_credit"]      # unknown, not absent
            else:
                parts["oi"] = min(1.0, max(0.0, oi / c["oi_full_credit_pct"])) * oi_trust

            pace = st.volume_pace()
            parts["volume"] = min(1.0, max(0.0, (pace - 0.8) / 0.7)) if pace else 0.0

            score = sum(parts[k] * w[k] for k in parts) / sum(w.values()) * 100
            if score < c["early_min_score"]:
                continue

            rows.append({
                "symbol": sym,
                "price": st.ltp,
                "dist_pct": max(dist_pct, 0.0),
                "score": round(score),
                "chg": chg,
                "gap": abs(st.gap_pct),
                "parts": {k: round(v, 2) for k, v in parts.items()},
                "pace": pace,
                "oi_pct": oi,
            })

        rows.sort(key=lambda r: (-r["score"], r["dist_pct"]))
        super_excellent = [r for r in rows if r["score"] >= c["jackpot_min_score"]]
        excellent = [r for r in rows
                     if c["strong_min_score"] <= r["score"] < c["jackpot_min_score"]]
        early_watch = [r for r in rows
                       if c["early_min_score"] <= r["score"] < c["strong_min_score"]]

        breadth_up = (advancing / total * 100) if total else None
        qualify_pct = (len(rows) / total * 100) if total else 0
        discriminating = qualify_pct <= c["saturation_pct"]

        # ULTRA-SAFE = SUPER EXCELLENT names with a tight gap - a big overnight
        # gap is its own risk on top of the breakout thesis, so a high score
        # alone isn't "safe" without checking the gap too.
        # MODERATE reuses the EXCELLENT-tier count.
        ultra_safe = len([r for r in super_excellent
                          if r["gap"] < c["ultra_safe_max_gap_pct"]])

        return {
            "super_excellent": super_excellent,
            "excellent": excellent,
            "early_watch": early_watch,
            "total_scanned": total,
            "qualify_pct": qualify_pct,
            "breadth_up_pct": breadth_up,
            "discriminating": discriminating,
            "ultra_safe": ultra_safe,
            "moderate": len(excellent),
            "regime": regime,
            "expiry": expiry_info,
            "ts": datetime.now(config.IST),
        }
