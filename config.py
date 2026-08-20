"""Central configuration for the Anticipatory Breakout Detector.

Concept: a scheduled digest, not a real-time trigger. A few times a day the
scanner ranks the F&O universe on how ready each stock looks to break its
20-day high, scores it 0-100, and Telegrams a shortlist in three tiers.

Five weighted inputs per stock (weights below):
  proximity   distance to the 20-day high, continuous - the most informative
              of the five, so it carries the heaviest weight
  vwap        price holding above the session VWAP
  trend       day change, sized against the stock's OWN ATR
  oi          futures OI building, discounted by expiry-cycle trust
  volume      day volume running ahead of its 20-day pace

The scoring block is the deployed parameter set, kept verbatim; everything
below the SCORING section is this project's own plumbing (data, schedule,
alerting).
"""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

IST = ZoneInfo("Asia/Kolkata")

# ============================== SCORING ====================================
# Deployed parameters (previously config.yaml: anticipatory_breakout).
NEAR_HIGH_LOOKBACK_DAYS = 20
MAX_DISTANCE_PCT = 10.0      # ignore names more than 10% below the high
MAX_EXTENSION_PCT = 1.0      # allow up to 1% past the high

# Whether the 20-day high counts TODAY's session (19 completed + today) or
# only the 20 COMPLETED sessions before it.
#
# Must stay False, and the spec proves it: a session's high is always >= its
# close, so including today forces high20 >= ltp and the distance can never go
# negative. That would make MAX_EXTENSION_PCT unreachable - yet the deployed
# spec documents it as live ("up to 1% past it", "too extended above is
# skipped"). A rule that can never fire is not a rule, so the original must
# measure against completed sessions only.
#
# Exposed as a switch because the two readings differ on real names (PETRONET
# on 2026-08-19: 0.00% away excluding today, 1.15% including it). Flip it only
# with evidence, and know that it disables the extension filter.
HIGH20_INCLUDES_TODAY = False
EARLY_MIN_SCORE = 55         # floor to appear at all -> EARLY WATCH
STRONG_MIN_SCORE = 70        # floor for EXCELLENT
JACKPOT_MIN_SCORE = 85       # floor for SUPER EXCELLENT
SATURATION_PCT = 35          # breadth sanity-check threshold
TREND_ATR_FRAC = 0.6         # full trend credit at 0.6x the stock's daily ATR
OI_FULL_CREDIT_PCT = 2.0     # full OI credit at +2% OI on the day
ULTRA_SAFE_MAX_GAP_PCT = 1.0 # SUPER EXCELLENT + gap under this = "ultra safe"

WEIGHTS = {
    "proximity": 2.0,
    "vwap": 1.0,
    "trend": 1.2,
    "oi": 1.3,
    "volume": 1.2,
}

# Score assigned to the OI leg when OI is unknown - "unknown, not absent".
# Deliberately below the 0.5 midpoint: no evidence of OI building is mildly
# bearish for a breakout thesis, but not disqualifying.
OI_UNKNOWN_CREDIT = 0.4

# ============================== SESSION ====================================
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
SESSION_MINUTES = 375                 # 09:15-15:30

# Digest START times (IST). `python main.py run` sleeps between these, and the
# Windows task triggers match them.
#
# Deliberately 4 minutes before the round slots the digest is "for": a full
# 208-symbol scan takes ~3.5 min, so starting at 11:00 delivered the message at
# 11:03 (measured 2026-08-20). Starting at 10:56 lands it on 11:00. If the scan
# gets slower - more symbols, or a wider throttle gap - widen this lead rather
# than accepting a late message.
DIGEST_TIMES = [time(10, 56), time(13, 26), time(15, 16)]

# ============================== DATA =======================================
BASE_TF_MIN = 5              # single 5-min feed; daily stats are resampled from it
# Calendar days of 5-min history per symbol. Needs >= 20 completed sessions for
# high20/avg-volume plus 14 more for ATR -> ~35 sessions -> ~55 calendar days.
HISTORY_DAYS = 60
CHUNK_DAYS = 90              # Firstock allows <= 100 days per 5mi request
MIN_SESSIONS_REQUIRED = 21   # 20 completed + today; below this the symbol is skipped
ATR_PERIOD = 14
# CORRECTED 2026-08-20: the throttle is on SPACING between calls, not on the
# number of parallel ones, and the "empty body / JSONDecodeError" symptom is an
# unparsed HTTP 429 whose body is the plain text `rate limit exceeded`.
# Measured on this account: 0.25s gap -> ~28% 429s, 1.0s -> ~20%, 1.5s -> 0/15.
#
# 4 workers x 0.05s is an effective gap of ~12ms, well inside the danger zone -
# the 11:00 run on 2026-08-20 dropped 8 of 208 symbols and took 208s. Retries
# recover most of it, but a proper fix is a SHARED min-gap limiter (~1.5-2.0s)
# across all threads plus a long backoff (2/4/6s); the current 1s/2s backoff in
# broker/firstock_data.py lands inside the same penalty window. That fix would
# serialize ~600 requests per scan, so budget 15-20 min per digest before
# enabling it - and widen the DIGEST_TIMES lead to match.
FETCH_WORKERS = 4
REQUEST_PAUSE_S = 0.05

# Universe: NSE F&O stocks (index contracts excluded - this scans STOCKS).
UNIVERSE_CACHE = "data/fno_universe_cache.json"
UNIVERSE_MAX_CACHE_AGE_DAYS = 5.0
# Drop a newline-separated symbol list here to override the NSE universe.
UNIVERSE_OVERRIDE_FILE = ROOT / "data" / "universe_override.txt"

# ============================== OPEN INTEREST ==============================
# The OI leg is worth 1.3/6.7 = 19% of the score. Firstock's getQuote carries
# `openInterest` for NFO futures but NO previous-day value, so the day change
# cannot come from the API alone: core/oi_store.py keeps a daily ledger and
# diffs today's reading against the last recorded session. That means the OI
# leg is dead on the FIRST ever run and starts working from the second session.
#
# While it is unavailable (first run, OI_ENABLED off, or a stock whose future
# will not resolve) every name gets the same OI_UNKNOWN_CREDIT. That freezes
# 19% of the score at a constant and compresses the spread between good and
# mediocre setups: tiers still rank correctly on the other four legs, but
# absolute scores read low and fewer names clear JACKPOT_MIN_SCORE.
OI_ENABLED = True
OI_EXPIRY_INDEX = 0          # 0 = nearest monthly future

# Expiry-cycle trust for the OI reading. Right after rollover the new series
# has barely any OI, so a large % change is noise; trust ramps in over the
# first few sessions of the cycle and is trimmed again in the last two days,
# when expiry-week unwinding muddies the "OI building = conviction" reading.
OI_TRUST_RAMP_DAYS = 5       # sessions after rollover to reach full trust
OI_TRUST_MIN = 0.3           # floor during the ramp
OI_EXPIRY_TAIL_DAYS = 2      # days before expiry that get trimmed trust
OI_TRUST_EXPIRY_TAIL = 0.5

# ============================== ALERTING ===================================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

ALERT_ENABLED = True
ALERT_EARLY_WATCH = False    # EARLY WATCH stays in the log/CSV, off the phone
TOP_N_PER_TIER = 20          # rows per tier in the message (auto-chunked)
# Suppress the whole digest when the scan is not discriminating (see
# SATURATION_PCT): if a third of the universe qualifies, the list is telling
# you the market is up, not which stocks are set up.
ALERT_ONLY_WHEN_DISCRIMINATING = True

# ============================== PATHS ======================================
LOG_DIR = ROOT / "logs"
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "digest_history.csv"
SNAPSHOT_FILE = DATA_DIR / "last_scan.json"
