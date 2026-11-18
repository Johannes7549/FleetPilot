# Risk scoring & trend methodology

All defaults below are **policy** (configurable per tenant in Settings → `company_settings.risk_config`),
not facts about the dataset.

## Trend rules (`app/analytics/trends.py`)

For each device and metric, over every snapshot in the window:

* **Persistence** — share of snapshots breaching the threshold
  * `insufficient_data` < 5 snapshots · `none` 0 breaches · `one_time` ≤ 1 breach ·
    `intermittent` > 1 breach but < 50% · `persistent` ≥ 50%
* **Direction** — least-squares slope per day: `deteriorating` / `stable` / `improving`
  (storage: ≤ −0.2 pp/day of free %; memory: ≥ +0.5 pp/day of used %)
* **Projection** — days until free disk reaches the critical threshold at the current slope.
* Compliance — per check: `persistent_failure` (failed every snapshot), `regressed` (pass→fail, still failing:
  *drift*), `recovered`, `failing`, `passing`; with transition dates.
* Battery — condition transitions with dates, relative full-charge-capacity change, cycle count. Missing
  battery blocks are counted as gaps, never as failures.

A single 95% RAM reading therefore yields `one_time` — not a persistent problem (eval T02).

## Category scores (0–100)

| Category | Rules |
|---|---|
| Storage | latest free % < 5 → 85 (confirmed); < 10 → 65 (confirmed); < 20 → 30; deteriorating → max(score, 45) + 15; persistent → +10 |
| Memory | persistent → 70, +15 if mean ≥ 90%; intermittent → 30; one-time spike → 10; rising trend only → 20 |
| Battery | service condition → 70 (+15 if cycles ≥ 1000, +10 if capacity −≥3%); otherwise cycles ≥ 1000 → 25, capacity decline → 30; no battery / unknown → 0 |
| Compliance | high-severity fail → 90; each medium fail +30 (cap 60); a regression (+10) |
| OS | major version below policy minimum (macOS 15, Windows 11) → 50 |

## Overall score & bands

```
overall = round( 0.5 × max(category scores) + 0.5 × Σ weight_c × score_c )
weights: storage 0.25 · memory 0.20 · battery 0.20 · compliance 0.20 · os 0.15  (must sum to 1)
bands:   critical ≥ 70 · at_risk ≥ 45 · watch ≥ 20 · healthy < 20
```

The max term stops one severe problem from being diluted by four healthy categories; the weighted mean lets
several moderate problems accumulate.

Each device result carries: overall score, classification, per-category score + `issue_type`
(`confirmed_failure` / `persistent` / `deteriorating` / `intermittent` / `one_time` / `policy`), contributing
factors (human-readable), evidence, and a recommended action (top category → upgrade order, replacement for
non-upgradeable Apple-silicon memory, remediation ticket, …), which still requires approval.
