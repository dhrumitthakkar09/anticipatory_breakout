"""OI ledger - the piece that makes a day-change possible at all."""
from __future__ import annotations

from datetime import date

import pytest

import config
from core import oi_store


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    """Never touch the real data/oi_history.json."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(oi_store, "STORE_FILE", tmp_path / "oi_history.json")


def test_first_ever_run_has_no_baseline():
    assert oi_store.update({"X": 1000.0}, today=date(2026, 8, 19)) == {"X": None}


def test_change_is_measured_against_the_previous_session():
    oi_store.update({"X": 1000.0}, today=date(2026, 8, 18))
    out = oi_store.update({"X": 1100.0}, today=date(2026, 8, 19))
    assert out["X"] == pytest.approx(10.0)


def test_later_scans_the_same_day_still_diff_against_yesterday():
    """Three digests a day must not diff against each other - the baseline
    stays the previous SESSION, or the 13:30 run would report ~0% change."""
    oi_store.update({"X": 1000.0}, today=date(2026, 8, 18))
    oi_store.update({"X": 1100.0}, today=date(2026, 8, 19))     # 11:00
    out = oi_store.update({"X": 1200.0}, today=date(2026, 8, 19))  # 13:30
    assert out["X"] == pytest.approx(20.0)


def test_last_write_of_a_day_becomes_tomorrows_baseline():
    oi_store.update({"X": 1000.0}, today=date(2026, 8, 18))
    oi_store.update({"X": 1200.0}, today=date(2026, 8, 18))      # closer to EOD
    out = oi_store.update({"X": 1320.0}, today=date(2026, 8, 19))
    assert out["X"] == pytest.approx(10.0)


def test_zero_baseline_is_unknown_not_infinite():
    oi_store.update({"X": 0.0}, today=date(2026, 8, 18))
    assert oi_store.update({"X": 500.0}, today=date(2026, 8, 19))["X"] is None


def test_history_is_trimmed_but_keeps_the_recent_window():
    for d in range(1, 20):
        oi_store.update({"X": 1000.0 + d}, today=date(2026, 8, d))
    import json
    hist = json.loads(oi_store.STORE_FILE.read_text(encoding="utf-8"))["X"]
    assert len(hist) == oi_store.KEEP_DATES
    assert "2026-08-19" in hist
