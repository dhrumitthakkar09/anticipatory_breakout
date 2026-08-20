# Anticipatory Breakout Detector

A scheduled digest that ranks the NSE F&O stock universe on how ready each
name looks to take out its **20-day high**, scores it 0-100, and Telegrams a
shortlist in three tiers.

It is **not** a real-time buy/sell trigger. It samples the market a few times a
day (11:00 / 13:30 / 15:20 by default) and hands you a ranked watchlist.

## Scoring

Five legs, each normalised to 0..1, combined as a weighted mean × 100:

| leg | weight | full credit at | why |
|---|---|---|---|
| `proximity` | **2.0** | at the 20-day high | continuous, not a yes/no — 0.4% away is not the same fact as 9.8% away, which is why it carries the heaviest weight |
| `oi` | 1.3 | +2% futures OI on the day | conviction behind the move, discounted by expiry-cycle trust |
| `trend` | 1.2 | day change ≥ 0.6 × the stock's own ATR% | a 2% day means something different on a calm stock than a wild one |
| `volume` | 1.2 | 1.5× the 20-day pace | ramps in from 0.8×; below that it scores zero |
| `vwap` | 1.0 | price above session VWAP | binary — intraday control |

Tiers: **EARLY WATCH** ≥ 55 · **EXCELLENT** ≥ 70 · **SUPER EXCELLENT** ≥ 85.

Two filters run before scoring: a stock more than `MAX_DISTANCE_PCT` (10%)
below its 20-day high is too far away to be "anticipating" anything, and one
more than `MAX_EXTENSION_PCT` (1%) *past* it has already gone.

### Why the 20-day high excludes today

`HIGH20_INCLUDES_TODAY = False`, and the spec proves it must be. A session's
high is always ≥ its close, so counting today forces `high20 >= ltp` and the
distance can never go negative — which would make `MAX_EXTENSION_PCT`
unreachable. The deployed spec documents that rule as live ("up to 1% past
it", "too extended above is skipped"), and a rule that can never fire is not a
rule.

The switch exists anyway, because the two readings do disagree on real names.
Measured over the full universe on 2026-08-19: tier counts identical (4 / 6 /
20), one swap in the top two tiers (PETRONET out at 72→68, YESBANK in at
63→70), largest move ±7 points.

Note the counter-intuitive direction — including today can *lower* `high20`
and raise a score. Both readings span exactly 20 sessions, so "19 completed +
today" drops the oldest completed session from the window; if that session
held the peak, the bar falls. That is what happened to YESBANK.

**ULTRA-SAFE** counts SUPER EXCELLENT names whose gap is under 1% — a big
overnight gap is its own risk stacked on top of the breakout thesis, so a high
score alone isn't "safe" without checking the gap too.

### The breadth check

`qualify_pct` is what share of the scanned universe cleared 55. Above
`SATURATION_PCT` (35%) the scan is flagged **SATURATED** and the digest is
suppressed: if a third of the F&O universe qualifies, the list is telling you
the market is up, not which stocks are set up.

## Commands

```
python main.py demo                    # synthetic data, no credentials, no network
python main.py check                   # login + universe + one-symbol data smoke test
python main.py scan --limit 15         # quick live pass over the first 15 symbols
python main.py scan --no-alert         # full universe, print only
python main.py scan                    # full universe + Telegram + CSV history
python main.py run                     # sit through today's digest slots
python -m pytest tests -q
```

Full universe (208 stocks) takes 2-3.5 minutes at `FETCH_WORKERS = 4`.

**Throttling caveat.** Firstock rate-limits on the *gap between* calls, not the
number of parallel ones. Over the limit it returns HTTP 429 whose body is the
plain text `rate limit exceeded` — not JSON — so it surfaces as a
`JSONDecodeError`, which reads like corrupt data but means throttling. At 4
workers x `REQUEST_PAUSE_S = 0.05` the effective gap is ~12ms, and the 11:00
run on 2026-08-20 lost 8 of 208 symbols to it. Retries recover most.

The real fix is a shared min-gap limiter (~1.5-2.0s across all threads) plus a
2/4/6s backoff — the current 1s/2s backoff lands inside the same penalty
window. That serializes ~600 requests per scan, so budget 15-20 minutes per
digest before enabling it, and widen the `DIGEST_TIMES` lead to match.

## Data

One Firstock 5-minute history call per symbol supplies everything price-based
— LTP, session VWAP, day volume, open/gap, previous close, 20-day high, 20-day
average volume and ATR. Firstock has no daily interval, so sessions are
resampled from the 5-min feed (same approach as `ema_confluence_scanner`).

The last session present in the data is treated as "today", so the scanner
still runs after hours and on holidays — it just re-reports the most recent
session instead of finding nothing.

### Open interest needs a day of warm-up

Firstock's `getQuote` returns a futures contract's **current** `openInterest`
and no previous-day value, so the day change cannot be computed from the API
alone. `core/oi_store.py` keeps a small JSON ledger (`data/oi_history.json`)
of one reading per symbol per date and diffs today against the last recorded
session.

**Consequence:** on the very first run every stock scores the unknown-OI
constant (0.4), which freezes 19% of the score and makes absolute scores read
low — tiers still rank correctly on the other four legs, but fewer names clear
85. The OI leg starts working on the **second** session. The reading stored
for a date is the last one written that day, so the 15:20 digest leaves the
closest thing to an end-of-day OI as tomorrow's baseline.

OI is stored in units (shares), not lots — percentage change is unaffected.

### Expiry-cycle trust

OI is only evidence when the series is mature. `core/expiry.py` discounts it
right after rollover (a new near-month future starts near-empty, so +40% OI is
a handful of lots) and in the last two days before expiry (unwinding and rolls
say nothing about direction). Stock futures are monthly — last Tuesday of the
month since the 2025 switchover, last Thursday before it.

## Configuration

Everything lives in [config.py](config.py). The SCORING block is the deployed
parameter set, kept verbatim; below it is this project's own plumbing.

Credentials come from `.env` (gitignored) — the same Firstock + Telegram keys
your other scanners use:

```
FIRSTOCK_USER_ID, FIRSTOCK_API_KEY, FIRSTOCK_VENDOR_CODE,
FIRSTOCK_TOTP_SECRET, FIRSTOCK_PASSWORD_SHA256
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
```

`config.json` (also gitignored) holds the Firstock session token and is
rewritten on login.

To pin the universe — a 10-stock run, or matching another tool exactly — drop
symbols one per line in `data/universe_override.txt`.

## Scheduling

`run_digest.bat` runs one full scan and exits; Windows Task Scheduler fires it
three times a day, Mon-Fri, via `run_digest_hidden.vbs`. That is more robust
than one long-lived process, since a crashed run cannot cost you the next one.

Triggers are **10:56 / 13:26 / 15:16** — four minutes before the round slots
the digest is "for". A full 208-symbol scan takes ~3.5 minutes, so firing at
11:00 delivered the message at 11:03 (measured 2026-08-20); the lead lands it
on the hour instead. `config.DIGEST_TIMES` carries the same start times, so
`python main.py run` behaves identically. If the scan gets slower — more
symbols, or the wider throttle gap discussed above — widen the lead rather
than accepting a late message.

Alternatively `python main.py run` holds a single process through all three
slots — a failed slot is logged and the next one still fires.

**TOTP collision:** every bot on this machine logs into the same Firstock
account with the same TOTP secret, and two logins inside one 30-second window
get rejected. The morning bots are staggered across 09:10-09:14; these digest
slots (11:00 / 13:30 / 15:20) sit well clear of them, so no new stagger is
needed — but keep it that way if you move the times.

## Known limitations

Two open issues, both observed on the 2026-08-20 11:00 run and neither fixed:

**1. Expiry rollover poisons the OI leg.** With August expiry three sessions
out, 86% of qualifying names reported *negative* OI (median -4.8%) — that is
the Aug→Sep roll, not traders abandoning positions. Any negative reading sets
`parts['oi'] = 0`, so 19% of the score was zeroed on 30 of 35 names, while
`oi_trust` stayed at 1.0 because `OI_EXPIRY_TAIL_DAYS = 2` had not engaged.
The visible symptom is backwards: on a day with 82% breadth up the scan
produced 1 SUPER EXCELLENT, against 4 the previous day at 31% breadth.

The real fix is to sum OI across the near *two* monthly futures, which nets a
rolling position to zero and answers the question the signal is actually
asking. Widening `OI_EXPIRY_TAIL_DAYS` to ~5 only damps the distortion.
Until then, treat expiry-week top tiers as artificially thin.

**2. Throttling drops symbols.** See the caveat under Commands — 8 of 208 lost
on that run. Dropped symbols are logged at DEBUG and counted at INFO, so
`logs/scanner.log` always shows how many a digest was built from.

## Output

* console — full digest with every score leg broken out
* Telegram — tiers with TradingView links (EARLY WATCH is off by default;
  `ALERT_EARLY_WATCH = True` to include it)
* `data/digest_history.csv` — every qualifying row, every run, with all five
  legs. This is the record that makes the weights reviewable later: did the
  SUPER EXCELLENT names actually break out?
* `data/last_scan.json` — the most recent raw result
* `logs/scanner.log`
