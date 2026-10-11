"""Inspected CFO forecasts and conservative scheduler ceilings, with integer USD units."""

from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

PERSONA_FIELDS = {"before_per_persona_microusd", "after_per_persona_microusd"}
OVERHEAD_FIELDS = {
    "discovery_microusd", "generation_microusd", "investigation_microusd",
    "repair_microusd", "known_replay_microusd",
}
FIELDS = PERSONA_FIELDS | OVERHEAD_FIELDS | {"event_budget_microusd"}  # Legacy imported contracts.
FORECAST_FIELDS = PERSONA_FIELDS | OVERHEAD_FIELDS | {
    "contingency_basis_points", "quote_budget_microusd", "lifetime_budget_microusd",
}
SCHEDULER_CAPS = {
    "discovery_microusd": 10_000, "generation_microusd": 10_000,
    "investigation_microusd": 0, "repair_microusd": 20_000, "known_replay_microusd": 10_000,
}


def digest(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value))


def timestamp(value):
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except (ValueError, TypeError):
        return False


def validate_plan(value):
    if not isinstance(value, dict) or not isinstance(value.get("source_url"), str):
        raise ValueError("CFO plan must be an inspected JSON object with its source URL.")
    parsed = urlsplit(value.get("source_url", ""))
    if (
        value.get("source") != "cfo.ai-inspected-plan"
        or parsed.scheme != "https" or parsed.hostname not in ("app.cfo.ai", "cfo.ai")
        or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None, 443)
        or not value.get("plan_id") or not digest(value.get("snapshot_sha256"))
    ):
        raise ValueError("CFO plan requires inspected plan provenance and accessible plan ID/URL.")
    version = value.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported CFO plan contract version.")
    assumptions = value.get("assumptions", {})
    fields = FORECAST_FIELDS if version == 2 else FIELDS
    if not isinstance(assumptions, dict) or set(assumptions) != fields or any(type(v) is not int or v < 0 for v in assumptions.values()):
        raise ValueError("CFO plan requires every nonnegative both-pass cost assumption.")
    if version == 2:
        if (
            value.get("status") != "finalized" or value.get("currency") != "USD"
            or not timestamp(value.get("inspected_at"))
            or not digest(value.get("inspection_evidence_sha256"))
        ):
            raise ValueError("CFO v2 import requires finalized, inspected USD forecast evidence.")
        if assumptions["contingency_basis_points"] > 10_000:
            raise ValueError("Contingency must be explicit basis points between 0 and 10000.")
        provenance = value.get("assumption_provenance", {})
        if not isinstance(provenance, dict) or set(provenance) != fields or any(
            not isinstance(p, dict) or p.get("basis") != "forecast"
            or not isinstance(p.get("reference"), str) or not p["reference"].strip() or not digest(p.get("evidence_sha256"))
            for p in provenance.values()
        ):
            raise ValueError("Every CFO assumption requires its inspected forecast row and evidence hash.")
        ledger = value.get("native_ledger_snapshot", {})
        balances = ("estimated_consumed_microusd", "held_microusd", "available_microusd")
        if (
            not isinstance(ledger, dict)
            or any(type(ledger.get(k)) is not int or ledger[k] < 0 for k in balances)
            or not timestamp(ledger.get("observed_at")) or not digest(ledger.get("evidence_sha256"))
            or sum(ledger[k] for k in balances) != assumptions["lifetime_budget_microusd"]
        ):
            raise ValueError("Native ledger snapshot must reconcile consumed + held + available to lifetime cap.")
    return value


def load_plan(path: Path | None):
    if not path or not path.exists():
        return None
    return validate_plan(json.loads(path.read_text()))


def contingency(subtotal, basis_points):
    return (subtotal * basis_points + 9_999) // 10_000


def allowance(settings, count):
    plan = load_plan(settings.cfo_plan_path)
    v2 = bool(plan and plan.get("schema_version") == 2)
    values = plan["assumptions"] if plan else {}
    before = count * max(settings.per_persona_microusd, values.get("before_per_persona_microusd", 0))
    after = count * max(settings.per_persona_microusd, values.get("after_per_persona_microusd", 0))
    caps = {
        k: max(floor, values.get(k, 0)) if v2 else floor
        for k, floor in SCHEDULER_CAPS.items()
    }
    overhead = max(
        settings.overhead_microusd,
        sum(caps.values()) if v2 else sum(values.get(k, 0) for k in OVERHEAD_FIELDS),
    )
    subtotal = before + after + overhead
    bps = values.get("contingency_basis_points", 0)
    extra = contingency(subtotal, bps)
    # v2 protects the complete after journey and known replay, including their own contingency.
    after_base = after + caps["known_replay_microusd"] if v2 else after
    after_extra = contingency(after_base, bps)
    legacy_limit = min(settings.budget_microusd, values.get("event_budget_microusd", settings.budget_microusd))
    lifetime_limit = min(settings.budget_microusd, values["lifetime_budget_microusd"]) if v2 else legacy_limit
    quote_limit = min(settings.budget_microusd, values["quote_budget_microusd"]) if v2 else legacy_limit
    comparison = None
    if v2:
        native_subtotal = count * (
            values["before_per_persona_microusd"] + values["after_per_persona_microusd"]
        ) + sum(values[k] for k in OVERHEAD_FIELDS)
        comparison = {
            "native_subtotal_microusd": native_subtotal,
            "native_reserved_microusd": native_subtotal + contingency(native_subtotal, bps),
            "scheduler_overhead_caps": caps,
            "native_overhead_caps": {k: values[k] for k in OVERHEAD_FIELDS},
            "native_ledger_snapshot": plan["native_ledger_snapshot"],
            "basis": "Inspected forecast, compared with current scheduler ledger; native available is not a lifetime cap.",
        }
    return {
        "before": before, "after": after, "overhead": overhead, "subtotal": subtotal,
        "contingency": extra, "contingency_basis_points": bps,
        "after_contingency": after_extra, "after_reserve": after_base + after_extra,
        "after_reserve_includes_known_replay": v2,
        "reserved": subtotal + extra, "limit": lifetime_limit, "quote_limit": quote_limit,
        "caps": {**caps, "before_per_persona_microusd": before // count if count else settings.per_persona_microusd,
                 "after_per_persona_microusd": after // count if count else settings.per_persona_microusd},
        "status": "imported" if plan else "missing",
        "basis": "CFO plan forecast compared with stricter scheduler ceilings" if plan else
                 "non-CFO local estimate; zero incremental API billing",
        "plan": plan, "forecast_comparison": comparison,
    }
