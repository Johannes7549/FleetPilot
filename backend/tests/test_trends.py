"""Temporal reasoning rules on synthetic series (the real dataset has no one-off RAM spikes, so the
spike rule is proven here)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.analytics.config import TrendRules
from app.analytics.trends import classify

T = [datetime(2026, 5, 1, tzinfo=UTC) + timedelta(days=i) for i in range(30)]
R = TrendRules()


def mem(values):
    return classify(T[: len(values)], values, lambda v: v > 80, R, bad_slope=0.5, higher_is_worse=True)


def test_single_spike_is_not_persistent():
    s = mem([50] * 14 + [95] + [50] * 15)
    assert s.persistence == "one_time" and s.label == "one_time_spike"


def test_intermittent():
    s = mem([50] * 10 + [85, 50, 90, 50, 88] + [50] * 15)
    assert s.persistence == "intermittent"


def test_persistent():
    s = mem([85] * 20 + [70] * 10)
    assert s.persistence == "persistent" and s.breach_count == 20


def test_rising_trend_without_breach_is_deteriorating():
    s = mem([40 + i for i in range(30)])
    assert s.persistence == "none" and s.direction == "deteriorating"


def test_storage_decline():
    vals = [17 - 0.5 * i for i in range(30)]
    s = classify(T, vals, lambda v: v < 10, R, bad_slope=-0.2, higher_is_worse=False)
    assert s.direction == "deteriorating" and s.latest_breaching
    assert abs(s.slope_per_day + 0.5) < 1e-6


def test_insufficient_data():
    assert mem([95, 95]).persistence == "insufficient_data"


def test_missing_values_ignored():
    s = classify(T[:6], [50, None, 50, None, 95, 50], lambda v: v > 80, R, bad_slope=0.5, higher_is_worse=True)
    assert s.n == 4 and s.persistence == "insufficient_data"
