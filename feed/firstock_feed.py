"""Build the scanner's two inputs from Firstock.

One 5-min history fetch per symbol supplies everything price-based: LTP,
session VWAP, day volume, the open (for the gap), the previous close, the
20-day high, the 20-day average volume and ATR. OI needs a second call
against the stock's nearest monthly future, and a diff against yesterday's
reading from core.oi_store.

Symbols that fail are dropped with a debug log rather than aborting the scan -
a 200-name digest should not be lost to one delisted contract.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from broker import firstock_data as fd
from core import daily_stats, oi_store
from core.state import StockState

log = logging.getLogger(__name__)


def _fetch_one(symbol: str) -> tuple[str, StockState, dict, float | None] | None:
    try:
        candles = fd.stock_candles(symbol)
    except Exception as e:                      # noqa: BLE001 - one bad symbol
        log.debug("%s: candle fetch failed: %s", symbol, e)
        return None
    if not candles:
        log.debug("%s: no candles returned", symbol)
        return None

    built = daily_stats.build(candles)
    if built is None:
        log.debug("%s: fewer than %d sessions of history",
                  symbol, config.MIN_SESSIONS_REQUIRED)
        return None
    state, stats = built

    oi = None
    if config.OI_ENABLED:
        try:
            oi = fd.future_oi(symbol)
        except Exception as e:                  # noqa: BLE001
            log.debug("%s: OI fetch failed: %s", symbol, e)

    time.sleep(config.REQUEST_PAUSE_S)
    return symbol, state, stats, oi


def build_inputs(symbols: list[str]) -> tuple[dict[str, StockState], dict[str, dict]]:
    """-> (states, daily_stats) keyed by symbol, ready for AnticipatoryBreakout."""
    states: dict[str, StockState] = {}
    stats: dict[str, dict] = {}
    oi_readings: dict[str, float] = {}

    fd.session()          # log in once, before the pool fans out
    with ThreadPoolExecutor(max_workers=config.FETCH_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in symbols}
        for fut in as_completed(futures):
            res = fut.result()
            if res is None:
                continue
            sym, state, st, oi = res
            states[sym] = state
            stats[sym] = st
            if oi is not None:
                oi_readings[sym] = oi

    missing = len(symbols) - len(states)
    if missing:
        log.info("%d/%d symbols dropped (no data or thin history)",
                 missing, len(symbols))

    # Single-threaded, after the pool: the OI ledger is one shared JSON file,
    # so writing it from the workers would need a lock for no benefit.
    if config.OI_ENABLED and oi_readings:
        changes = oi_store.update(oi_readings)
        for sym, chg in changes.items():
            if sym in states:
                states[sym].oi_change_pct = chg
        known = sum(1 for v in changes.values() if v is not None)
        if states and not known:
            log.warning("No OI baseline yet - the OI leg (%.0f%% of the score) is a "
                        "constant this run and scores read low. It starts working "
                        "on the next session.",
                        config.WEIGHTS["oi"] / sum(config.WEIGHTS.values()) * 100)
    return states, stats
