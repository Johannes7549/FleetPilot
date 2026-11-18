# Tool catalog

_Generated from the live registry by `python scripts/gen_tool_catalog.py`._

Every tool: Pydantic-validated arguments (`extra=forbid`), tenant taken from the authenticated session (any `company_id`-like argument is rejected), permission check, bounded retry on transient DB errors, timing, and a structured `ToolResult` = `{ok, summary, data, evidence[], error, error_code, duration_ms, attempts}`. Error codes: `invalid_arguments`, `forbidden_argument`, `permission_denied`, `not_found`, `insufficient_evidence`, `validation_failed`, `database_error`, `timeout`, `tool_error`, `tenant_violation`.

## Read-only tools (permission `telemetry:read`)

### `get_fleet_overview`

Fleet-wide summary for the caller's company: device/employee/snapshot counts, observation window, platform and OS mix, risk classification counts, and latest compliance failure counts.

Example requests: *How many devices do we have?*; *Give me a fleet overview*

No arguments.

### `get_device_details`

Inventory details, latest snapshot measurements and risk score for one device in the caller's company.

Example requests: *Tell me about device 8NM23J95R5I6*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `device_id` | string | yes | — | Device ID (serial), e.g. 8NM23J95R5I6 |

### `find_low_storage_devices`

Devices whose latest snapshot is below an available-disk threshold, with 30-day trend (persistent / deteriorating) and projected days to critical.

Example requests: *Which devices are low on disk space?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `threshold_pct` | number (>0, <100) | no | null | Available-% threshold; default is policy low_pct |
| `include_deteriorating` | boolean | no | True | Also list devices above threshold but trending down |

### `analyze_memory_usage`

RAM utilisation per device across all snapshots; classifies persistent pressure vs one-time spikes vs intermittent, with mean/peak usage.

Example requests: *Which devices have consistently high RAM usage?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `threshold_pct` | number (>0, ≤100) | no | null | Used-% threshold; default policy high_pct |
| `only_flagged` | boolean | no | True | Only devices exceeding the threshold at least once |

### `analyze_battery_health`

Battery condition, cycle count, full-charge-capacity change and condition transitions per device; devices without battery telemetry are reported as unknown, not failing.

Example requests: *Which employees have devices with battery service recommendations?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `only_flagged` | boolean | no | True | Only devices needing service or with declining capacity |

### `get_compliance_status`

Latest compliance check results per device, filterable by check_id, severity and status.

Example requests: *Show me devices failing high-severity compliance checks*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `check_id` | string | no | null | e.g. disk_encryption, screen_lock, os_up_to_date |
| `severity` | high \| medium \| low | no | null |  |
| `status` | fail \| pass \| any | no | fail |  |

### `analyze_compliance_trends`

Compliance drift over the window: pass->fail regressions (with dates), recoveries and persistent failures.

Example requests: *Is compliance getting worse over time?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `check_id` | string | no | null |  |

### `get_os_distribution`

OS family/version distribution from latest snapshots, version changes over the window, and devices older than a given major version.

Example requests: *How many devices are running an OS older than macOS 15?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `os_name` | macOS \| Windows | no | null | Restrict to one OS family |
| `older_than_major` | integer (≥1, ≤100) | no | null | List devices whose major version is below this |

### `get_installed_software`

Installed software from latest snapshots (optionally by name or device), install counts, and when watch-listed packages first appeared.

Example requests: *Which devices have uTorrent installed?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `name` | string | no | null | Software name substring |
| `device_id` | string | no | null |  |
| `watchlist_only` | boolean | no | False |  |

### `generate_fleet_insights`

Fleet insights (persistent problems, deteriorating trends, compliance drift, policy gaps) with severity, evidence and recommended next steps, plus the highest-risk devices.

Example requests: *What are the biggest fleet health risks this month?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `category` | storage \| memory \| battery \| compliance \| os \| software \| data_quality | no | null |  |
| `top_devices` | integer (≥0, ≤50) | no | 5 | How many highest-risk devices to include |

### `get_risk_scores`

Deterministic per-device risk scores (0-100) with category breakdown, contributing factors and the recommended action.

Example requests: *Which devices are critical?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `classification` | critical \| at_risk \| watch \| healthy | no | null |  |
| `limit` | integer (≥1, ≤200) | no | 25 |  |

### `get_device_history`

Full time series for one device (disk %, RAM %, battery condition/cycles, compliance, OS) with trend classification.

Example requests: *Show the history of 8NM23J95R5I6*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `device_id` | string | yes | — |  |
| `metric` | storage \| memory \| battery \| compliance \| os \| all | no | all |  |
| `days` | integer (≥1, ≤365) | no | null | Only the last N days of the window |

### `get_employee_devices`

Devices assigned to one employee in the caller's company, with each device's risk classification.

Example requests: *Which devices does emp-acme-1003 have?*

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `employee_id` | string | yes | — |  |

## Action tools (permission `actions:propose`; create `pending_approval` proposals only)

### `create_upgrade_order`

Propose a hardware upgrade order (memory, storage or battery) for a device. Creates a pending_approval proposal; refused without supporting telemetry.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `device_id` | string | yes | — |  |
| `component` | memory \| storage \| battery | yes | — |  |
| `spec` | string | yes | — | Requested specification, e.g. '1 TB SSD' |

### `open_remediation_ticket`

Propose a remediation ticket for a compliance check that is failing on a device in its latest snapshot. Creates a pending_approval proposal.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `device_id` | string | yes | — |  |
| `check_id` | string | yes | — |  |
| `note` | string | yes | — |  |

### `flag_device_for_replacement`

Propose flagging a device for replacement (requires at_risk or critical risk). Creates a pending_approval proposal.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `device_id` | string | yes | — |  |
| `reason` | string | yes | — |  |

### `notify_employee`

Propose a notification to an employee about findings on their devices. Creates a pending_approval proposal.

| Argument | Type | Required | Default | Description |
|---|---|---|---|---|
| `employee_id` | string | yes | — |  |
| `message` | string | yes | — |  |
