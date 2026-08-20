"""Digest rendering: console, Telegram, CSV history, JSON snapshot."""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime

import requests

import config

log = logging.getLogger(__name__)

TIER_LABELS = [
    ("super_excellent", "SUPER EXCELLENT", "🟢"),
    ("excellent", "EXCELLENT", "🔵"),
    ("early_watch", "EARLY WATCH", "⚪"),
]


# --------------------------------------------------------------------------- #
#  Console                                                                     #
# --------------------------------------------------------------------------- #
def format_console(res: dict) -> str:
    exp = res.get("expiry") or {}
    lines = [
        "=" * 74,
        f"  ANTICIPATORY BREAKOUT   {res['ts']:%d-%b-%Y %H:%M} IST",
        "=" * 74,
        f"  scanned {res['total_scanned']}   qualified {res['qualify_pct']:.1f}%"
        f"   breadth up {res['breadth_up_pct'] or 0:.0f}%"
        f"   {'discriminating' if res['discriminating'] else 'SATURATED'}",
        f"  ultra-safe {res['ultra_safe']}   moderate {res['moderate']}"
        f"   OI trust {exp.get('oi_trust', 1.0)} ({exp.get('reason', '-')})",
    ]
    if not res["discriminating"]:
        lines.append(f"  ! over {config.SATURATION_PCT}% of the universe qualified - "
                     "this is a market move, not a shortlist")

    for key, label, _ in TIER_LABELS:
        rows = res[key]
        lines.append(f"\n  -- {label} ({len(rows)}) --")
        if not rows:
            lines.append("     (none)")
            continue
        for r in rows[:config.TOP_N_PER_TIER]:
            pace = f"{r['pace']:.2f}x" if r["pace"] else "  n/a"
            oi = f"{r['oi_pct']:+.1f}%" if r["oi_pct"] is not None else "  n/a"
            lines.append(
                f"     {r['symbol']:<14}{r['score']:>3}   ₹{r['price']:>9,.1f}"
                f"   {r['dist_pct']:>5.2f}% to high   chg {r['chg']:>+5.1f}%"
                f"   gap {r['gap']:>4.1f}%   vol {pace}   OI {oi}")
            lines.append(f"                    {r['parts']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
#  Telegram                                                                    #
# --------------------------------------------------------------------------- #
def send_telegram(text: str) -> bool:
    if not config.TELEGRAM_ENABLED:
        log.warning("Telegram not configured; skipping alert")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok")
        if not ok:
            log.error("Telegram send failed: %s", r.text[:200])
        return bool(ok)
    except requests.RequestException as e:
        log.error("Telegram send error: %s", e)
        return False


def _row_line(r: dict) -> str:
    tv = f"https://in.tradingview.com/chart/?symbol=NSE%3A{r['symbol'].replace('&', '_')}"
    bits = [f"{r['dist_pct']:.1f}% to high", f"{r['chg']:+.1f}%"]
    if r["pace"]:
        bits.append(f"vol {r['pace']:.1f}x")
    if r["oi_pct"] is not None:
        bits.append(f"OI {r['oi_pct']:+.1f}%")
    if r["gap"] >= config.ULTRA_SAFE_MAX_GAP_PCT:
        bits.append(f"gap {r['gap']:.1f}%")
    return (f"<b>{r['score']}</b> <a href=\"{tv}\">{r['symbol']}</a> "
            f"₹{r['price']:,.1f} · " + " · ".join(bits))


def _chunk(header: str, lines: list[str], limit: int = 3800) -> list[str]:
    """Split into Telegram-sized messages, each carrying the header."""
    out, cur, size = [], [], len(header)
    for ln in lines:
        if cur and size + len(ln) + 1 > limit:
            out.append("\n".join([header] + cur))
            cur, size = [], len(header)
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        out.append("\n".join([header] + cur))
    return out


def send_digest(res: dict) -> int:
    """Telegram the shortlist. -> number of rows sent (0 = nothing sent)."""
    if not config.ALERT_ENABLED:
        return 0
    if config.ALERT_ONLY_WHEN_DISCRIMINATING and not res["discriminating"]:
        log.info("Digest suppressed: %.1f%% of the universe qualified (> %d%%)",
                 res["qualify_pct"], config.SATURATION_PCT)
        return 0

    tiers = [t for t in TIER_LABELS
             if t[0] != "early_watch" or config.ALERT_EARLY_WATCH]
    lines: list[str] = []
    for key, label, mark in tiers:
        rows = res[key][:config.TOP_N_PER_TIER]
        if not rows:
            continue
        lines.append(f"{mark} <b>{label}</b> ({len(res[key])})")
        lines.extend(_row_line(r) for r in rows)
    if not lines:
        log.info("Digest empty: nothing cleared score %d", config.EARLY_MIN_SCORE)
        return 0

    header = (f"📈 <b>ANTICIPATORY BREAKOUT</b> {res['ts']:%d-%b %H:%M} · "
              f"{res['total_scanned']} scanned · breadth {res['breadth_up_pct'] or 0:.0f}% up · "
              f"ultra-safe {res['ultra_safe']}")
    msgs = _chunk(header, lines)
    if not all(send_telegram(m) for m in msgs):
        return 0
    return sum(len(res[k]) for k, _, _ in tiers)


# --------------------------------------------------------------------------- #
#  Persistence                                                                 #
# --------------------------------------------------------------------------- #
def _tier_of(res: dict, symbol: str) -> str:
    for key, label, _ in TIER_LABELS:
        if any(r["symbol"] == symbol for r in res[key]):
            return label
    return ""


def log_history(res: dict) -> None:
    """Append every qualifying row - the record that makes the weights
    reviewable later (did SUPER EXCELLENT names actually break out?)."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    new = not config.HISTORY_FILE.exists()
    ts = res["ts"].strftime("%Y-%m-%d %H:%M")
    with open(config.HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "tier", "symbol", "score", "price",
                        "dist_pct", "chg_pct", "gap_pct", "pace", "oi_pct",
                        "proximity", "vwap", "trend", "oi", "volume",
                        "breadth_up_pct", "qualify_pct"])
        for key, label, _ in TIER_LABELS:
            for r in res[key]:
                p = r["parts"]
                w.writerow([ts, label, r["symbol"], r["score"],
                            round(r["price"], 2), round(r["dist_pct"], 2),
                            round(r["chg"], 2), round(r["gap"], 2),
                            round(r["pace"], 2) if r["pace"] else "",
                            round(r["oi_pct"], 2) if r["oi_pct"] is not None else "",
                            p["proximity"], p["vwap"], p["trend"], p["oi"], p["volume"],
                            round(res["breadth_up_pct"] or 0, 1),
                            round(res["qualify_pct"], 1)])


def save_snapshot(res: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, default=str)


def publish(res: dict, telegram: bool = True) -> None:
    """Everything that happens to a finished scan.

    `telegram=False` suppresses only the message - the console digest, the CSV
    history and the snapshot are always written, since a dry run you cannot
    review afterwards is worth very little.
    """
    print(format_console(res))
    log_history(res)
    save_snapshot(res)
    if not telegram:
        return
    sent = send_digest(res)
    log.info("Digest: %d rows alerted at %s", sent,
             datetime.now(config.IST).strftime("%H:%M"))
