"""Daily open-interest history, so the OI leg has something to diff against.

Firstock's getQuote returns the CURRENT openInterest of a contract and no
previous-day value, so day-change cannot be computed from a single call. This
keeps a small JSON ledger of one OI reading per symbol per date and diffs
today against the most recent earlier date.

Consequences worth knowing:
  * the very first run has no baseline, so every symbol scores the unknown-OI
    constant - the OI leg starts contributing from the SECOND session onward;
  * the reading stored for a date is the LAST one written that day, so the
    15:20 digest leaves the closest thing to an end-of-day OI as tomorrow's
    baseline;
  * OI is stored in units (shares), not lots. Percentage change is unaffected,
    but do not read the raw numbers as contract counts.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import config

log = logging.getLogger(__name__)

STORE_FILE = config.DATA_DIR / "oi_history.json"
KEEP_DATES = 10          # per symbol; only the previous session is ever read


def _load() -> dict[str, dict[str, float]]:
    try:
        with open(STORE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save(store: dict[str, dict[str, float]]) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=0, sort_keys=True)
    tmp.replace(STORE_FILE)          # atomic: a killed run cannot truncate history


def update(readings: dict[str, float], today: date | None = None
           ) -> dict[str, float | None]:
    """Record today's OI per symbol and return each one's day change in %.

    `readings` is {symbol: current open interest}. A symbol with no earlier
    date on file, or a zero baseline, gets None - unknown, which the scanner
    scores as a constant rather than as zero change.
    """
    today = (today or date.today()).isoformat()
    store = _load()
    changes: dict[str, float | None] = {}

    for sym, oi in readings.items():
        hist = store.setdefault(sym, {})
        prior = [d for d in sorted(hist) if d < today]
        baseline = hist[prior[-1]] if prior else None

        if oi is not None:
            hist[today] = oi
            for stale in sorted(hist)[:-KEEP_DATES]:
                del hist[stale]

        if oi is None or not baseline:
            changes[sym] = None
        else:
            changes[sym] = (oi - baseline) / baseline * 100

    _save(store)
    known = sum(1 for v in changes.values() if v is not None)
    log.info("OI history: %d/%d symbols have a previous-session baseline",
             known, len(readings))
    return changes
