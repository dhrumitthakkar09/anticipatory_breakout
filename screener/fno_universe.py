"""NSE F&O stock universe.

Primary source: NSE's fo_mktlots.csv. The file also lists index contracts
(NIFTY, BANKNIFTY, ...) which we exclude - this scans STOCKS. Result is cached
to JSON so a dead NSE endpoint never blocks a scheduled digest.

Drop a newline-separated symbol list in data/universe_override.txt to pin the
universe (handy for a quick 10-stock run, or to match another tool exactly).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

import config

log = logging.getLogger(__name__)

MKTLOTS_URLS = [
    "https://archives.nseindia.com/content/fo/fo_mktlots.csv",
    "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
]
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/csv,*/*",
}
_INDEX_SYMBOLS = {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}
# NSE keeps adding index contracts (NIFTYFPI showed up in the Aug-2026 file and
# slipped past an explicit list, then failed as NSE:NIFTYFPI-EQ "not found in
# database"). No F&O STOCK symbol starts with NIFTY, so match the prefix
# instead of chasing new names one at a time.
_INDEX_PREFIXES = ("NIFTY",)

_cache_path = Path(config.ROOT) / config.UNIVERSE_CACHE


def _parse_mktlots(text: str) -> dict[str, int]:
    """-> {symbol: current-month lot size} for individual securities only."""
    lots: dict[str, int] = {}
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[1]:
            continue
        sym = parts[1].upper()
        if (sym == "SYMBOL" or sym in _INDEX_SYMBOLS
                or sym.startswith(_INDEX_PREFIXES)):
            continue
        for p in parts[2:]:                  # first numeric column = near-month lot
            if p.replace(" ", "").isdigit():
                lots[sym] = int(p.replace(" ", ""))
                break
    return lots


def _download() -> dict[str, int] | None:
    for url in MKTLOTS_URLS:
        try:
            r = requests.get(url, headers=_HEADERS, timeout=15)
            if r.ok and "," in r.text:
                lots = _parse_mktlots(r.text)
                if len(lots) > 50:           # sanity: the list is ~180-230 stocks
                    return lots
        except requests.RequestException:
            continue
    return None


def _override() -> list[str] | None:
    path = config.UNIVERSE_OVERRIDE_FILE
    if not path.exists():
        return None
    syms = [ln.strip().upper() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    return syms or None


def load_universe(max_cache_age_days: float = config.UNIVERSE_MAX_CACHE_AGE_DAYS
                  ) -> dict[str, int]:
    """{symbol: lot_size} for all NSE F&O stocks."""
    if _cache_path.exists():
        age_days = (time.time() - _cache_path.stat().st_mtime) / 86400
        if age_days <= max_cache_age_days:
            with open(_cache_path, encoding="utf-8") as f:
                return json.load(f)

    lots = _download()
    if lots:
        _cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(_cache_path, "w", encoding="utf-8") as f:
            json.dump(lots, f, indent=1, sort_keys=True)
        return lots

    if _cache_path.exists():                 # stale cache beats no data
        log.warning("NSE unreachable; using stale universe cache")
        with open(_cache_path, encoding="utf-8") as f:
            return json.load(f)
    raise RuntimeError(
        "Could not download NSE fo_mktlots.csv and no local cache exists. "
        f"Save it manually to {_cache_path} as JSON {{symbol: lot_size}}, or "
        f"list symbols in {config.UNIVERSE_OVERRIDE_FILE}.")


def symbols() -> list[str]:
    """The scan universe, override first."""
    ov = _override()
    if ov:
        log.info("Universe override in use: %d symbols", len(ov))
        return ov
    return sorted(load_universe())
