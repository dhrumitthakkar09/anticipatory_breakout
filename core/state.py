"""Per-symbol intraday state - the exact surface the scanner reads.

The scanner duck-types whatever it is handed, so this is the contract: keep
these five attributes and the volume_pace() method and any data source can be
swapped in behind it (Firstock today, a websocket tick feed later).
"""
from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass
class StockState:
    ltp: float
    vwap: float | None
    day_change_pct: float             # vs previous close
    gap_pct: float                    # open vs previous close (sign kept)
    oi_change_pct: float | None = None    # None = unknown, scored as a constant
    day_volume: float = 0.0
    avg20_volume: float = 0.0
    minutes_elapsed: int = config.SESSION_MINUTES

    def volume_pace(self) -> float | None:
        """Today's volume against where the 20-day average would be by now.

        1.0 = on pace, >1.0 = running hot. None when the average is unknown,
        which the scanner scores as 0 for the volume leg.

        The pro-rating is LINEAR in elapsed minutes, while real intraday
        volume is U-shaped - heavy at the open and close, thin midday. So the
        11:00 digest reads slightly hot and the 13:30 one slightly cold. The
        scanner only uses pace above 0.8 and saturates at 1.5, so the bias
        moves scores by a few points, not tiers; a session volume curve would
        fix it properly if that ever matters.
        """
        if not self.avg20_volume or not self.minutes_elapsed:
            return None
        expected = self.avg20_volume * (min(self.minutes_elapsed, config.SESSION_MINUTES)
                                        / config.SESSION_MINUTES)
        if not expected:
            return None
        return self.day_volume / expected
