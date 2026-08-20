"""Minimal candle type shared by the feed, the daily-stats layer and tests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Candle:
    start: datetime          # candle open time (naive IST)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def typical(self) -> float:
        """(H+L+C)/3 - the price VWAP is accumulated on."""
        return (self.high + self.low + self.close) / 3

    @property
    def range(self) -> float:
        return self.high - self.low
