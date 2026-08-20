"""One scan pass: universe -> Firstock -> scanner result."""
from __future__ import annotations

import logging
import time

import config
from core.expiry import expiry_info
from core.scanner import AnticipatoryBreakout
from feed.firstock_feed import build_inputs
from screener.fno_universe import symbols as universe_symbols

log = logging.getLogger(__name__)


def run_scan(symbols: list[str] | None = None, regime: str | None = None) -> dict:
    syms = symbols or universe_symbols()
    log.info("Scanning %d symbols...", len(syms))

    t0 = time.time()
    states, stats = build_inputs(syms)
    log.info("Data ready for %d symbols in %.1fs", len(states), time.time() - t0)
    if not states:
        raise RuntimeError(
            "No symbol returned usable data. Check Firstock credentials "
            "(`python main.py check`) and that config.json holds a live jKey.")

    exp = expiry_info()
    log.info("OI trust %.2f (%s)", exp["oi_trust"], exp["reason"])

    res = AnticipatoryBreakout().scan(states, stats, regime=regime, expiry_info=exp)
    log.info("Scored %d/%d qualifying (%.1f%%), breadth %.0f%% up",
             len(res["super_excellent"]) + len(res["excellent"]) + len(res["early_watch"]),
             res["total_scanned"], res["qualify_pct"], res["breadth_up_pct"] or 0)
    return res


def configure_logging(verbose: bool = False) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(config.LOG_DIR / "scanner.log", encoding="utf-8")],
    )
