"""Scheduled digest runner - sleeps until each slot in config.DIGEST_TIMES.

This is a digest, not a trigger: it deliberately samples the market a few
times a day rather than streaming. Run it once in the morning and it will sit
through the session firing at each slot, then exit after the last one.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

import alerts
import config
from scan import run_scan

log = logging.getLogger(__name__)


def _next_slot(now: datetime) -> datetime | None:
    for t in config.DIGEST_TIMES:
        slot = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if slot > now:
            return slot
    return None


def run_day(alert: bool = True) -> None:
    now = datetime.now(config.IST)
    log.info("Scheduled digests today: %s",
             ", ".join(t.strftime("%H:%M") for t in config.DIGEST_TIMES))

    while True:
        now = datetime.now(config.IST)
        slot = _next_slot(now)
        if slot is None:
            log.info("All digest slots done for %s - exiting", now.date())
            return
        wait = (slot - now).total_seconds()
        log.info("Next digest %s (in %s)", slot.strftime("%H:%M"),
                 str(timedelta(seconds=int(wait))))
        time.sleep(wait)

        try:
            res = run_scan()
        except Exception:                       # noqa: BLE001 - one bad slot
            # A failed 11:00 fetch must not cost the 13:30 and 15:20 digests.
            log.exception("Digest at %s failed", slot.strftime("%H:%M"))
            continue
        alerts.publish(res, telegram=alert)
