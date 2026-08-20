"""Anticipatory Breakout Detector - CLI.

Usage:
    python main.py demo                       # synthetic data, no creds needed
    python main.py check                      # login + universe + data smoke test
    python main.py scan [--symbols A,B,C] [--limit N] [--no-alert]
    python main.py run [--no-alert]           # sit through the day's digest slots
"""
from __future__ import annotations

import argparse
import sys

import config

# The digest prints ₹ and tier marks; a stock Windows console is cp1252 and
# raises UnicodeEncodeError on both. Nothing is lost by forcing UTF-8 here.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def cmd_demo(args) -> None:
    import alerts
    import demo
    from scan import configure_logging

    configure_logging(args.verbose)
    res = demo.run()
    print(alerts.format_console(res))
    print("  (synthetic data - no Firstock call was made)\n")


def cmd_check(args) -> None:
    from scan import configure_logging

    configure_logging(args.verbose)
    from broker import firstock_data as fd
    from core import oi_store
    from core.daily_stats import build
    from core.expiry import expiry_info
    from screener.fno_universe import symbols as universe_symbols

    syms = universe_symbols()
    print(f"Universe: {len(syms)} F&O stocks (first 5: {syms[:5]})")

    fd.session()
    print("Firstock login OK")

    probe = "RELIANCE" if "RELIANCE" in syms else syms[0]
    candles = fd.stock_candles(probe)
    print(f"{probe}: {len(candles)} x {config.BASE_TF_MIN}-min candles over "
          f"{config.HISTORY_DAYS}d, last {candles[-1].start if candles else '-'}")

    oi = fd.future_oi(probe) if config.OI_ENABLED else None
    futs = fd.search_futures(probe)
    print(f"{probe}: futures {[f['symbol'] for f in futs[:3]]} -> "
          f"OI {f'{oi:,.0f}' if oi else 'unavailable'}")
    change = oi_store.update({probe: oi}).get(probe) if oi else None
    print(f"{probe}: OI day change "
          + (f"{change:+.2f}%" if change is not None else
             f"UNAVAILABLE - no previous-session baseline in "
             f"{oi_store.STORE_FILE.name} yet, so the OI leg scores the "
             f"{config.OI_UNKNOWN_CREDIT} constant until tomorrow"))

    built = build(candles, oi_change_pct=change)
    if built is None:
        print(f"{probe}: NOT enough history "
              f"(need {config.MIN_SESSIONS_REQUIRED} sessions)")
    else:
        st, ds = built
        pace, vwap = st.volume_pace(), st.vwap
        print(f"{probe}: ltp {st.ltp:.2f}  "
              f"vwap {f'{vwap:.2f}' if vwap else 'n/a'}  "
              f"chg {st.day_change_pct:+.2f}%  gap {st.gap_pct:+.2f}%  "
              f"pace {f'{pace:.2f}x' if pace else 'n/a'}")
        print(f"{probe}: high20 {ds['high20']:.2f}  atr {ds['atr_pct']:.2f}%  "
              f"sessions {ds['sessions']}  as_of {ds['as_of']}")

    exp = expiry_info()
    print(f"Expiry cycle: prev {exp['prev_expiry']} next {exp['next_expiry']} "
          f"-> OI trust {exp['oi_trust']} ({exp['reason']})")
    print(f"Telegram: {'configured' if config.TELEGRAM_ENABLED else 'NOT configured'}")


def cmd_scan(args) -> None:
    import alerts
    from scan import configure_logging, run_scan

    configure_logging(args.verbose)
    syms = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    if syms is None and args.limit:
        from screener.fno_universe import symbols as universe_symbols
        syms = universe_symbols()[:args.limit]

    res = run_scan(symbols=syms)
    alerts.publish(res, telegram=not args.no_alert)


def cmd_run(args) -> None:
    from runner.scheduled import run_day
    from scan import configure_logging

    configure_logging(args.verbose)
    run_day(alert=not args.no_alert)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo")
    sub.add_parser("check")

    sc = sub.add_parser("scan")
    sc.add_argument("--symbols", type=str, default="",
                    help="comma-separated subset (default: full F&O universe)")
    sc.add_argument("--limit", type=int, default=0,
                    help="scan only the first N symbols - a quick smoke run")
    sc.add_argument("--no-alert", action="store_true",
                    help="suppress the Telegram message; console, CSV history "
                         "and snapshot are still written")

    rn = sub.add_parser("run")
    rn.add_argument("--no-alert", action="store_true")

    args = ap.parse_args()
    {"demo": cmd_demo, "check": cmd_check,
     "scan": cmd_scan, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
