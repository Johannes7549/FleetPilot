"""Transparent time-series rules (no ML). Two independent facets per metric series:

persistence — how often the policy threshold is breached across the window
    insufficient_data : fewer than `min_snapshots` points
    none              : never breached
    one_time          : breached in <= `one_time_max_breaches` snapshots (a spike, not a problem)
    intermittent      : breached more than once but in < `persistent_ratio` of snapshots
    persistent        : breached in >= `persistent_ratio` of snapshots

direction — least-squares slope of the metric per day
    deteriorating / stable / improving, compared with a configured slope threshold
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np

from app.analytics.config import TrendRules


@dataclass
class TrendSummary:
    n: int
    breach_count: int
    breach_ratio: float
    persistence: str
    direction: str
    slope_per_day: float | None
    first_value: float | None
    latest_value: float | None
    min_value: float | None
    max_value: float | None
    mean_value: float | None
    latest_breaching: bool
    first_breach_index: int | None

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def label(self) -> str:
        """Single headline label, most serious facet first."""
        if self.persistence == "persistent" and self.direction == "deteriorating":
            return "persistent_and_deteriorating"
        if self.persistence == "persistent":
            return "persistent"
        if self.direction == "deteriorating":
            return "deteriorating"
        if self.persistence == "intermittent":
            return "intermittent"
        if self.persistence == "one_time":
            return "one_time_spike"
        if self.persistence == "insufficient_data":
            return "insufficient_data"
        return "normal"


def slope_per_day(times: Sequence[datetime], values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    t0 = times[0]
    x = np.array([(t - t0).total_seconds() / 86400 for t in times], dtype=float)
    y = np.array(values, dtype=float)
    if np.ptp(x) == 0:
        return None
    return float(np.polyfit(x, y, 1)[0])


def classify(
    times: Sequence[datetime],
    values: Sequence[float | None],
    breach: Callable[[float], bool],
    rules: TrendRules,
    *,
    bad_slope: float,
    higher_is_worse: bool,
) -> TrendSummary:
    pairs = [(t, float(v)) for t, v in zip(times, values, strict=True) if v is not None]
    n = len(pairs)
    if n == 0:
        return TrendSummary(0, 0, 0.0, "insufficient_data", "unknown", None, None, None, None, None, None, False, None)
    ts = [p[0] for p in pairs]
    vs = [p[1] for p in pairs]
    flags = [breach(v) for v in vs]
    count = sum(flags)
    ratio = count / n
    slope = slope_per_day(ts, vs)

    if n < rules.min_snapshots:
        persistence = "insufficient_data"
    elif count == 0:
        persistence = "none"
    elif ratio >= rules.persistent_ratio:
        persistence = "persistent"
    elif count <= rules.one_time_max_breaches:
        persistence = "one_time"
    else:
        persistence = "intermittent"

    direction = "unknown"
    if slope is not None and n >= rules.min_snapshots:
        worse = slope >= bad_slope if higher_is_worse else slope <= bad_slope
        better = slope <= -bad_slope if higher_is_worse else slope >= -bad_slope
        direction = "deteriorating" if worse else "improving" if better else "stable"

    return TrendSummary(
        n=n,
        breach_count=count,
        breach_ratio=round(ratio, 3),
        persistence=persistence,
        direction=direction,
        slope_per_day=None if slope is None else round(slope, 4),
        first_value=round(vs[0], 2),
        latest_value=round(vs[-1], 2),
        min_value=round(min(vs), 2),
        max_value=round(max(vs), 2),
        mean_value=round(sum(vs) / n, 2),
        latest_breaching=flags[-1],
        first_breach_index=flags.index(True) if count else None,
    )
