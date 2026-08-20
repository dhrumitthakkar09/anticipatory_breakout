"""Firstock V1 market-data client - equity candles + futures OI.

Candle / search / quote calls go through direct `requests` + `json.loads`
because the `firstock` SDK parses responses with `ast.literal_eval`, which
crashes on JSON `null` (see project memory: reference-firstock-api).

Firstock quirks baked in:
  * timePriceSeries `interval` must be n+"mi" ("1mi", "5mi") - there is no
    daily interval, so daily stats are resampled from the 5-min feed.
  * start/end format is "HH:MM:SS DD-MM-YYYY" (time FIRST).
  * window limits: 1mi <= 31 days/request, 5mi <= 100 days/request.
  * NSE equity tradingSymbol needs the -EQ suffix (RELIANCE-EQ).
  * stock FUTURES tradingSymbol: <SYM><DD><MMM><YY>F, e.g. RELIANCE26AUG26F.
"""
from __future__ import annotations

import json
import logging
import re
import time as _time
from datetime import datetime, timedelta, timezone

import requests

import config
from core.candles import Candle

from .firstock_session import FirstockSession

log = logging.getLogger(__name__)

BASE = "https://api.firstock.in/V1"
IST = timezone(timedelta(hours=5, minutes=30))

_session: FirstockSession | None = None


def session() -> FirstockSession:
    global _session
    if _session is None:
        _session = FirstockSession().ensure()
    return _session


def _session_expired(resp: dict) -> bool:
    err = resp.get("error") if isinstance(resp, dict) else None
    msg = (err.get("message", "") if isinstance(err, dict) else str(err or "")).lower()
    return ("session expired" in msg or "invalid credentials" in msg
            or "user validation failed" in msg)


def _post(path: str, payload: dict, retries: int = 2) -> dict:
    s = session()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        body = {**payload, "userId": s.user_id, "jKey": s.jkey()}
        try:
            r = requests.post(f"{BASE}/{path}", json=body, timeout=30)
            resp = json.loads(r.content.decode("utf-8"))  # null-safe (SDK is not)
        except (requests.RequestException, json.JSONDecodeError) as e:
            last_err = e
            _time.sleep(1.0 + attempt)
            continue
        # tokens die at Firstock's daily boundary - re-login once and retry
        if _session_expired(resp) and attempt < retries:
            s.login()
            continue
        return resp
    raise RuntimeError(f"Firstock {path} failed after retries: {last_err}")


def _fmt(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S %d-%m-%Y")


def _num(row: dict, *keys) -> float | None:
    for k in keys:
        v = row.get(k)
        if v not in (None, "", "NA"):
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


# --------------------------------------------------------------------------- #
#  Candles                                                                     #
# --------------------------------------------------------------------------- #
def _rows_to_candles(resp: dict) -> list[Candle]:
    data = resp.get("data") if isinstance(resp, dict) else None
    if not isinstance(data, list):
        return []
    out: list[Candle] = []
    for row in data:
        epoch = row.get("epochTime") or row.get("ssboe") or row.get("time")
        try:
            ts = datetime.fromtimestamp(int(float(epoch)), tz=timezone.utc).astimezone(IST)
        except (TypeError, ValueError):
            continue
        o = _num(row, "into", "open")
        h = _num(row, "inth", "high")
        lo = _num(row, "intl", "low")
        c = _num(row, "intc", "close", "lp")
        v = _num(row, "intv", "volume", "v") or 0.0
        if None in (o, h, lo, c):
            continue
        out.append(Candle(ts.replace(tzinfo=None), o, h, lo, c, v))
    out.sort(key=lambda c: c.start)
    # drop the zero-range 15:30 boundary row timePriceSeries appends
    return [c for c in out if not (c.volume == 0 and c.open == c.high == c.low == c.close
                                   and c.start.hour == 15 and c.start.minute == 30)]


def candles_window(exchange: str, tradingsymbol: str, tf_min: int,
                   start: datetime, end: datetime) -> list[Candle]:
    """Raw window fetch. Caller must respect Firstock's per-request limits
    (31 days for 1mi, 100 days for 5mi) - use `candles_chunked` for long spans."""
    resp = _post("timePriceSeries", {
        "exchange": exchange, "tradingSymbol": tradingsymbol,
        "startTime": _fmt(start), "endTime": _fmt(end),
        "interval": f"{tf_min}mi",
    })
    if isinstance(resp, dict) and resp.get("status") not in ("success", None):
        err = resp.get("error") or resp
        raise RuntimeError(f"timePriceSeries {tradingsymbol} failed: {err}")
    return _rows_to_candles(resp)


def candles_chunked(exchange: str, tradingsymbol: str, tf_min: int,
                    start: datetime, end: datetime) -> list[Candle]:
    """Long-span fetch split into windows Firstock accepts."""
    max_days = 25 if tf_min == 1 else config.CHUNK_DAYS
    out: list[Candle] = []
    cur = start
    while cur < end:
        chunk_end = min(cur + timedelta(days=max_days), end)
        out.extend(candles_window(exchange, tradingsymbol, tf_min, cur, chunk_end))
        cur = chunk_end
    seen: set[datetime] = set()      # chunk boundaries can overlap
    return [c for c in sorted(out, key=lambda c: c.start)
            if not (c.start in seen or seen.add(c.start))]


def stock_candles(symbol: str, tf_min: int = config.BASE_TF_MIN,
                  days: int = config.HISTORY_DAYS) -> list[Candle]:
    """Recent candles for an NSE equity ('RELIANCE' -> 'RELIANCE-EQ')."""
    end = datetime.now()
    start = end - timedelta(days=days)
    return candles_chunked("NSE", f"{symbol}-EQ", tf_min, start, end)


# --------------------------------------------------------------------------- #
#  Futures OI                                                                  #
# --------------------------------------------------------------------------- #
# <SYM><DD><MMM><YY>F  e.g. RELIANCE26AUG26F
_FUT_RE = re.compile(r"^([A-Z0-9&\-]+?)(\d{2})([A-Z]{3})(\d{2})F$")

_fut_cache: dict[str, list[dict]] = {}


def _parse_future_symbol(sym: str, underlying: str) -> dict | None:
    m = _FUT_RE.match(sym or "")
    if not m or m.group(1) != underlying:
        return None
    _, dd, mmm, yy = m.groups()
    try:
        exp = datetime.strptime(f"{dd}{mmm}{yy}", "%d%b%y").date()
    except ValueError:
        return None
    return {"symbol": sym, "expiry": exp}


def search_futures(symbol: str) -> list[dict]:
    """All listed monthly futures for a stock, nearest expiry first."""
    if symbol in _fut_cache:
        return _fut_cache[symbol]
    resp = _post("searchScrips", {"stext": f"{symbol} FUT"})
    data = resp.get("data") if isinstance(resp, dict) else None
    today = datetime.now().date()
    out: list[dict] = []
    if isinstance(data, list):
        for row in data:
            if row.get("exchange") != "NFO":
                continue
            parsed = _parse_future_symbol(row.get("tradingSymbol", ""), symbol)
            if parsed and parsed["expiry"] >= today:
                out.append(parsed)
    out.sort(key=lambda f: f["expiry"])
    _fut_cache[symbol] = out
    return out


def future_oi(symbol: str,
              expiry_index: int = config.OI_EXPIRY_INDEX) -> float | None:
    """Current open interest of the stock's nearest monthly future, in units.

    getQuote carries `openInterest` but NO previous-day value, so the day
    CHANGE cannot come from this call - core.oi_store keeps the daily history
    that makes a diff possible. Returns None when there is no listed future or
    the quote has no OI field; the scanner treats that as unknown, not zero.
    """
    futs = search_futures(symbol)
    if not futs:
        return None
    fut = futs[min(expiry_index, len(futs) - 1)]
    try:
        resp = _post("getQuote", {"exchange": "NFO", "tradingSymbol": fut["symbol"]})
    except RuntimeError as e:
        log.debug("getQuote %s failed: %s", fut["symbol"], e)
        return None
    data = resp.get("data") if isinstance(resp, dict) else None
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return None
    return _num(data, "openInterest", "oi", "openinterest")
