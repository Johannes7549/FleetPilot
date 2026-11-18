"""Deterministic intent classification, entity extraction and planning.

Used directly in deterministic mode and as the validated fallback whenever the LLM planner fails.
Rules are intentionally explicit so evaluation results are reproducible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

INTENTS = ("fleet_query", "insight_request", "device_lookup", "action_request", "help", "out_of_scope",
           "unsupported_data")

DEVICE_ID_RE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{10,14}\b")
EMPLOYEE_ID_RE = re.compile(r"\bemp-[a-z0-9]+-\d+\b", re.IGNORECASE)
PCT_RE = re.compile(r"(?:below|under|less than|<|lower than|above|over|more than|>|exceed(?:s|ing)?)\s*(\d{1,2}(?:\.\d+)?)\s*%")
OS_OLDER_RE = re.compile(r"(?:older than|earlier than|below|before|prior to|less than|<)\s*(macos|windows)\s*(\d{1,2})", re.I)
OS_OLDER_RE2 = re.compile(r"(macos|windows)\s*(?:version\s*)?(?:older than|earlier than|below|before|<)\s*(\d{1,2})", re.I)

CHECK_ALIASES = {
    "screen_lock": ("screen lock", "screen_lock", "screenlock", "lock screen", "auto-lock", "auto lock"),
    "disk_encryption": ("encryption", "encrypted", "disk_encryption", "filevault", "bitlocker"),
    "os_up_to_date": ("os_up_to_date", "os up to date", "up-to-date", "up to date", "os update", "patch"),
}

TOPICS: dict[str, tuple[str, ...]] = {
    "storage": ("disk", "storage", "space", "ssd", "drive", "full", "capacity"),
    "memory": ("ram", "memory", "swap"),
    "battery": ("battery", "batteries", "charge", "cycle"),
    "compliance": ("complian", "failing", "fail ", "encrypt", "screen lock", "screen_lock", "security",
                   "non-compliant", "noncompliant", "policy violation"),
    "os": ("macos", "windows", "operating system", " os ", " os?", "os version", "os older", "outdated os",
           "os distribution", "version"),
    "software": ("software", "installed", "application", " app ", "apps", "package", "utorrent", "teamviewer",
                 "cleanmymac", "chrome", "slack", "zoom", "1password", "vs code", "visual studio"),
    "insight": ("risk", "insight", "biggest", "worst", "attention", "priorit", "concern", "health", "problem",
                "issue", "anomal", "unusual", "summary of", "what's wrong", "what is wrong"),
    "overview": ("overview", "how many devices", "how many employees", "fleet size", "number of devices",
                 "total devices", "how many laptops", "how many snapshots", "summar"),
    "trend": ("trend", "over time", "drift", "getting worse", "deteriorat", "regress", "changed", "history",
              "historical", "consistently", "persistent", "this month", "past 30", "last 30"),
}

UNSUPPORTED = {
    "cpu": "CPU utilisation", "processor usage": "CPU utilisation", "uptime": "uptime", "crash": "crash reports",
    "warranty": "warranty", "purchase date": "purchase dates", "price": "cost/price", "cost": "cost/price",
    "location": "device location", "gps": "device location", "salary": "employee HR data",
    "email address": "employee contact details", "phone number": "employee contact details",
    "temperature": "thermal sensors", "bandwidth": "network performance", "latency": "network performance",
    "login": "login/authentication events", "vpn": "VPN status", "antivirus": "antivirus status",
}

ACTION_LEAD = re.compile(
    r"^\s*(?:please\s+|kindly\s+|go ahead and\s+|let'?s\s+|i (?:want|need|would like) (?:you )?to\s+|"
    r"(?:can|could|would|will) you\s+(?:please\s+)?)?"
    r"(create|open|raise|file|submit|place|send|notify|message|email|flag|order|propose|log|request|replace|upgrade|schedule)\b",
    re.IGNORECASE,
)
HELP_RE = re.compile(r"^\s*(help|what can you do|what do you do|how do i use|what questions|capabilities)", re.I)
GREETING_RE = re.compile(r"^\s*(hi|hello|hey|thanks|thank you)\b[\s!.]*$", re.I)


@dataclass
class Entities:
    device_ids: list[str] = field(default_factory=list)
    employee_ids: list[str] = field(default_factory=list)
    check_id: str | None = None
    severity: str | None = None
    threshold_pct: float | None = None
    os_name: str | None = None
    older_than_major: int | None = None
    software_name: str | None = None
    topics: list[str] = field(default_factory=list)
    action_type: str | None = None
    component: str | None = None
    unsupported: list[str] = field(default_factory=list)
    risk_class: str | None = None


@dataclass
class PlanStep:
    tool: str
    args: dict[str, Any]
    purpose: str

    def as_dict(self) -> dict:
        return {"tool": self.tool, "args": self.args, "purpose": self.purpose}


def extract_entities(question: str, known_software: list[str] | None = None) -> Entities:
    q = f" {question.lower()} "
    e = Entities()
    e.device_ids = list(dict.fromkeys(DEVICE_ID_RE.findall(question)))
    e.employee_ids = list(dict.fromkeys(m.lower() for m in EMPLOYEE_ID_RE.findall(question)))
    for check, aliases in CHECK_ALIASES.items():
        if any(a in q for a in aliases):
            e.check_id = check
            break
    for sev in ("high", "medium", "low"):
        if re.search(rf"\b{sev}[- ]severity\b|\bseverity[: ]+{sev}\b|\b{sev}[- ]risk compliance\b", q):
            e.severity = sev
    m = PCT_RE.search(q)
    if m:
        e.threshold_pct = float(m.group(1))
    m = OS_OLDER_RE.search(question) or OS_OLDER_RE2.search(question)
    if m:
        e.os_name = "macOS" if m.group(1).lower() == "macos" else "Windows"
        e.older_than_major = int(m.group(2))
    elif "macos" in q:
        e.os_name = "macOS"
    elif "windows" in q:
        e.os_name = "Windows"
    for name in sorted(known_software or [], key=len, reverse=True):
        if name.lower() in q:
            e.software_name = name
            break
    e.topics = [t for t, kws in TOPICS.items() if any(k in q for k in kws)]
    if e.check_id and "compliance" not in e.topics:
        e.topics.append("compliance")
    if e.software_name and "software" not in e.topics:
        e.topics.append("software")
    e.unsupported = sorted({label for k, label in UNSUPPORTED.items() if re.search(rf"\b{re.escape(k)}\b", q)})
    for cls, kws in (("critical", ("critical",)), ("at_risk", ("at risk", "at-risk"))):
        if any(k in q for k in kws):
            e.risk_class = cls
    # action semantics
    if re.search(r"\b(ticket|remediat)", q):
        e.action_type = "open_remediation_ticket"
    elif re.search(r"\b(replace|replacement|flag)\b", q) and "battery" not in q:
        e.action_type = "flag_device_for_replacement"
    elif re.search(r"\b(notify|message|email|tell|inform|alert)\b", q):
        e.action_type = "notify_employee"
    elif re.search(r"\b(upgrade|order|replace|replacement)\b", q):
        e.action_type = "create_upgrade_order"
    if re.search(r"\b(ram|memory)\b", q):
        e.component = "memory"
    elif re.search(r"\b(disk|storage|ssd|drive|space)\b", q):
        e.component = "storage"
    elif "batter" in q:
        e.component = "battery"
    return e


def classify_intent(question: str, e: Entities) -> tuple[str, float]:
    if HELP_RE.search(question) or GREETING_RE.search(question):
        return "help", 0.95
    if ACTION_LEAD.search(question) and e.action_type:
        return "action_request", 0.9
    if e.unsupported and not (set(e.topics) - {"insight", "trend", "overview"}):
        return "unsupported_data", 0.8
    if e.device_ids and not (set(e.topics) - {"trend", "insight"}):
        return "device_lookup", 0.9
    if e.employee_ids and not (set(e.topics) - {"trend", "insight"}):
        return "device_lookup", 0.85
    domain = set(e.topics) - {"trend"}
    if not domain and e.risk_class:
        return "fleet_query", 0.8
    if not domain and not e.device_ids and not e.employee_ids:
        if "trend" in e.topics:
            return "insight_request", 0.6
        return "out_of_scope", 0.7
    if domain == {"insight"} or (domain <= {"insight", "overview"} and "insight" in domain):
        return "insight_request", 0.85
    return "fleet_query", 0.85


def plan(question: str, intent: str, e: Entities) -> list[PlanStep]:
    if intent in ("help", "out_of_scope", "unsupported_data"):
        return []
    steps: list[PlanStep] = []
    topics = set(e.topics)
    trend = "trend" in topics

    def add(tool: str, args: dict, purpose: str) -> None:
        if not any(s.tool == tool and s.args == args for s in steps):
            steps.append(PlanStep(tool, args, purpose))

    for d in e.device_ids[:5]:
        add("get_device_details", {"device_id": d}, f"Inventory, latest snapshot and risk for {d}")
        if trend or topics & {"storage", "memory", "battery", "compliance", "os"}:
            metric = next((t for t in ("storage", "memory", "battery", "compliance", "os") if t in topics), "all")
            add("get_device_history", {"device_id": d, "metric": metric}, f"Historical {metric} telemetry for {d}")
    for emp in e.employee_ids[:5]:
        add("get_employee_devices", {"employee_id": emp}, f"Devices assigned to {emp}")

    device_scoped = bool(e.device_ids)
    if "storage" in topics and not device_scoped:
        args = {"threshold_pct": e.threshold_pct} if e.threshold_pct else {}
        add("find_low_storage_devices", args, "Latest free-disk % per device vs threshold, with 30-day trend")
    if "memory" in topics and not device_scoped:
        args = {"threshold_pct": e.threshold_pct} if e.threshold_pct and "storage" not in topics else {}
        add("analyze_memory_usage", args, "RAM utilisation across all snapshots; persistent vs spikes")
    if "battery" in topics and not device_scoped:
        add("analyze_battery_health", {}, "Battery condition, cycles and capacity trend")
    if "compliance" in topics and not device_scoped:
        if trend:
            add("analyze_compliance_trends", {"check_id": e.check_id} if e.check_id else {},
                "Pass->fail transitions and failure-rate trend")
        args: dict[str, Any] = {"status": "fail"}
        if e.check_id:
            args["check_id"] = e.check_id
        if e.severity:
            args["severity"] = e.severity
        add("get_compliance_status", args, "Latest compliance results matching the filters")
    if "os" in topics and not device_scoped and not (topics == {"os", "compliance"} and e.check_id == "os_up_to_date"):
        args = {}
        if e.os_name:
            args["os_name"] = e.os_name
        if e.older_than_major:
            args["older_than_major"] = e.older_than_major
        add("get_os_distribution", args, "OS version distribution from latest snapshots")
    if "software" in topics:
        args = {}
        if e.software_name:
            args["name"] = e.software_name
        if e.device_ids:
            args["device_id"] = e.device_ids[0]
        if re.search(r"unapproved|watch|suspicious|risky|unauthori", question, re.I):
            args["watchlist_only"] = True
        add("get_installed_software", args, "Installed software inventory")
    if e.risk_class:
        add("get_risk_scores", {"classification": e.risk_class}, f"Devices classified {e.risk_class}")
    if intent == "insight_request" or ("insight" in topics and not steps):
        add("generate_fleet_insights", {}, "Fleet-wide insights and highest-risk devices")
    if ("overview" in topics and not (topics - {"overview", "trend", "insight"})) or not steps:
        add("get_fleet_overview", {}, "Fleet counts, observation window and risk summary")
    return steps


def action_gathering_plan(e: Entities) -> list[PlanStep]:
    """For action requests: gather evidence with read tools before proposing anything."""
    steps: list[PlanStep] = []
    for d in e.device_ids[:10]:
        steps.append(PlanStep("get_device_details", {"device_id": d}, f"Verify {d} and gather its telemetry"))
    for emp in e.employee_ids[:10]:
        steps.append(PlanStep("get_employee_devices", {"employee_id": emp}, f"Verify {emp} and their devices"))
    if not e.device_ids and not e.employee_ids:
        comp = e.component
        if e.action_type == "open_remediation_ticket" or "compliance" in e.topics:
            args: dict[str, Any] = {"status": "fail"}
            if e.check_id:
                args["check_id"] = e.check_id
            steps.append(PlanStep("get_compliance_status", args, "Find devices failing the check"))
        elif comp == "storage":
            steps.append(PlanStep("find_low_storage_devices", {"include_deteriorating": False}, "Find low-storage devices"))
        elif comp == "memory":
            steps.append(PlanStep("analyze_memory_usage", {}, "Find persistently RAM-constrained devices"))
        elif comp == "battery":
            steps.append(PlanStep("analyze_battery_health", {}, "Find batteries needing service"))
        else:
            cls = e.risk_class or "critical"
            steps.append(PlanStep("get_risk_scores", {"classification": cls}, f"Find {cls} devices"))
    return steps
