"""Closed outbound summaries. Full local evidence never passes through this policy."""

import hashlib
import json
import re
from pathlib import Path
from threading import Lock

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SynchronousMultiSpanProcessor
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext

POLICY = "tryfolk.outbound.v1"
WORKFLOW = "tryfolk-local-runtime"
ENDPOINT = "https://ingest.neatlogs.com/v1/traces"
SCOPE = "neatlogs.tryfolk"
HEX_ID = re.compile(r"[a-f0-9]{32}")
HASH = re.compile(r"[a-f0-9]{64}")
REVISION = re.compile(r"(?:sha256:)?[a-f0-9]{40,64}")
KINDS = frozenset({
    "discovery", "persona_initialized", "scenario_fixture", "observation", "decision",
    "browser_action", "scenario_payment_condition", "capability", "persona_memory", "verification",
    "repair", "known_replay_action", "known_replay_capability", "known_failure_replay",
    "held_out_checks", "connection_recovery", "baseline_population_reused", "conditional_purchase_plan", "known_replay_reused",
    "native_pay_admission", "hosted_checkout_admission",
})
CLASSIFICATIONS = frozenset({
    "verification_unavailable", "correlation_mismatch", "payment_unsettled", "payment_and_entitlement",
    "entitlement_correlation_mismatch", "paid_without_access", "awaiting_entitlement", "decline_handled",
    "unpaid_access", "persona_abandoned", "ambiguous_order",
})
OUTCOMES = frozenset({"completed", "failed", "abandoned", "pending", "blocked"})
BOOLS = (
    "measured", "payment_verified", "entitlement_verified", "capability_checked", "capability_available",
    "diagnostic", "not_in_population_denominator", "no_additional_provider_checkout", "capability_separate",
    "step_limit",
)


def mapping(value):
    return value if isinstance(value, dict) else {}


def json_mapping(value):
    if not isinstance(value, str) or len(value) > 2_000_000:
        return {}
    try:
        return mapping(json.loads(value))
    except (ValueError, RecursionError):
        return {}


def copy_bool(result, data, keys):
    for key in keys:
        if type(data.get(key)) is bool or key in ("capability_available", "available") and data.get(key) is None:
            if key in data:
                result[key] = data[key]


def copy_count(result, data, keys):
    for key in keys:
        if type(data.get(key)) is int and 0 <= data[key] <= 1_000_000_000:
            result[key] = data[key]


def copy_enum(result, data, key, allowed):
    if isinstance(data.get(key), str) and data[key] in allowed:
        result[key] = data[key]


def list_count(result, data, key, output):
    if isinstance(data.get(key), list):
        result[output] = len(data[key])


def copy_hashes(result, data, keys):
    for key in keys:
        if isinstance(data.get(key), str) and HASH.fullmatch(data[key]):
            result[key] = data[key]


def context_without_state(context):
    if context is None:
        return None
    return SpanContext(context.trace_id, context.span_id, context.is_remote, context.trace_flags)


class BoundaryProcessors(SynchronousMultiSpanProcessor):
    """OTel's public processor seam seals metadata the SDK mask cannot inspect.

    Neatlogs Client accepts this private TracerProvider. No SDK internals or
    global providers are patched. All real processors receive the same clone;
    Client(mask=...) still applies the final data projection after backfill.
    """

    def __init__(self, policy):
        super().__init__()
        self.policy = policy

    def on_end(self, span):
        attributes = dict(span.attributes or {})
        if attributes.pop("neatlogs.mask_id", None):
            self.policy.reject("per_span_mask_override")
        original_scope = span.instrumentation_scope
        scope = original_scope.name if original_scope else None
        if scope not in (SCOPE, "tryfolk", "neatlogs.internal"):
            scope = "tryfolk.rejected_scope"
            attributes["tryfolk.scope_rejected"] = True
            self.policy.reject("scope_rejected")
        clean = ReadableSpan(
            name=span.name, context=context_without_state(span.context),
            parent=context_without_state(span.parent),
            resource=Resource(dict(span.resource.attributes)),
            attributes=attributes, events=span.events,
            links=[Link(context_without_state(link.context), attributes={})
                   for link in span.links if link.context.trace_id == span.context.trace_id],
            kind=span.kind, status=span.status, start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=InstrumentationScope(scope),
        )
        super().on_end(clean)


class TracePolicy:
    """One run-bound mask, with bounded local rejection reasons instead of raw text."""

    def __init__(self, run, evidence_root=None):
        self.evidence_root = Path(evidence_root).resolve() if evidence_root else None
        self.context = {"policy": POLICY}
        copy_bool(self.context, run, ("diagnostic", "not_in_population_denominator"))
        for key, field in (("run_id", "id"), ("population_id", "population_id")):
            if isinstance(run.get(field), str) and HEX_ID.fullmatch(run[field]):
                self.context[key] = run[field]
        for key, allowed in (("provider", {"dodo", "local-fixture"}), ("pass", {"before", "after"})):
            if run.get(key) in allowed:
                self.context[key] = run[key]
        if isinstance(run.get("revision"), str) and REVISION.fullmatch(run["revision"]):
            self.context["revision"] = run["revision"]
        if type(run.get("persona_count")) is int and 1 <= run["persona_count"] <= 1000:
            self.context["N"] = run["persona_count"]
        lineage = mapping(run.get("baseline_retry"))
        original = run.get("retry_of_run_id")
        self._baseline_reuse = None
        if (
            self.context.get("provider") == "dodo" and self.context.get("pass") == "before"
            and self.context.get("run_id") and self.context.get("population_id")
            and isinstance(original, str) and HEX_ID.fullmatch(original)
            and lineage.get("original_run_id") == original
            and lineage.get("retry_run_id") == self.context.get("run_id")
            and lineage.get("population_id") == self.context.get("population_id")
            and isinstance(lineage.get("population_sha256"), str)
            and HASH.fullmatch(lineage["population_sha256"]) and self.context.get("N")
        ):
            self._baseline_reuse = {
                "original_run_id": original, "population_id": lineage["population_id"],
                "population_sha256": lineage["population_sha256"], "requested": self.context["N"],
                "initial_state": "saved_initial_snapshot", "generation_performed": False,
            }
        self._gates = set()
        self._lock = Lock()
        self._persona_intents = {}
        assignments = mapping(run.get("scenario_plan")).get("assignments", [])
        self._scenario_hashes = {
            a["scenario_sha256"] for a in assignments if isinstance(a, dict)
            and isinstance(a.get("scenario_sha256"), str) and HASH.fullmatch(a["scenario_sha256"])
        } if isinstance(assignments, list) else set()

    def bind_purchase_persona(self, persona):
        """Bind an inferred intention to its saved persona, before emitting it."""
        from .bulk_personas import validated_purchase_plan

        plan = validated_purchase_plan(persona)
        binding = (plan["profile_sha256"], plan["scenario_sha256"])
        with self._lock:
            previous = self._persona_intents.get(persona["id"])
            if previous is not None and previous != binding:
                raise ValueError("Saved persona intention binding changed within the run.")
            self._persona_intents[persona["id"]] = binding

    @property
    def gates(self):
        with self._lock:
            return sorted(self._gates)

    def reject(self, reason):
        with self._lock:
            self._gates.add(reason)

    def persisted_hash(self, value):
        """Hash an existing bounded owned evidence file; never export its path."""
        if not self.evidence_root or not isinstance(value, str):
            return None
        try:
            path = Path(value).resolve(strict=True)
            if not path.is_relative_to(self.evidence_root) or not path.is_file():
                return None
            with path.open("rb") as source:
                content = source.read(20_000_001)
            if len(content) <= 20_000_000:
                return hashlib.sha256(content).hexdigest()
        except (OSError, ValueError):
            pass
        return None

    def summary(self, kind, data, persona_id=None):
        result = {}
        copy_bool(result, data, ("diagnostic", "not_in_population_denominator"))
        if kind in ("scenario_fixture", "scenario_payment_condition"):
            binding = mapping(data.get("binding"))
            if (self.context.get("run_id") and persona_id
                    and binding.get("run_id") == self.context["run_id"]
                    and binding.get("persona_id") == persona_id
                    and binding.get("target_revision") == self.context.get("revision")):
                copy_hashes(result, binding, (
                    "scenario_sha256", "saved_persona_sha256", "profile_sha256", "initial_memory_sha256",
                    "condition_fixture_sha256",
                ))
        if kind == "conditional_purchase_plan":
            plan = mapping(data.get("plan"))
            copy_hashes(result, plan, ("profile_sha256", "scenario_sha256"))
            copy_bool(result, plan, ("purchase",))
            copy_count(result, plan, ("max_steps",))
            copy_enum(result, plan, "unexpected_state", {"reconsider", "abandon"})
            copy_enum(result, plan, "source", {"genuine_codex_batch_intent"})
            copy_bool(result, data, ("payment_outcome_inferred",))
        elif kind == "hosted_checkout_admission":
            copy_enum(result, data, "phase", {"wait", "enter", "exit"})
            copy_enum(result, data, "result", {"owned_return_settled", "failed", "cancelled", "unused"})
            if data.get("capacity") == 1 and type(data.get("capacity")) is int:
                result["capacity"] = 1
            copy_count(result, data, ("wait_ms", "duration_ms"))
            if type(data.get("queue_bound_ms")) is int and data["queue_bound_ms"] == 300_000:
                result["queue_bound_ms"] = 300_000
            if data.get("payment_outcome_inferred") is False:
                result["payment_outcome_inferred"] = False
        elif kind == "native_pay_admission":
            copy_enum(result, data, "phase", {"wait", "enter", "exit"})
            copy_enum(result, data, "result", {"owned_return_settled", "billing_controls_settled", "failed", "cancelled", "unused"})
            copy_enum(result, data, "boundary_kind", {"pay", "billing_continue"})
            if data.get("capacity") == 1 and type(data.get("capacity")) is int:
                result["capacity"] = 1
            copy_count(result, data, ("wait_ms", "duration_ms", "cooldown_ms", "configured_minimum_spacing_ms"))
            copy_enum(result, data, "spacing_basis", {"conservative_experiment"})
            if data.get("payment_outcome_inferred") is False:
                result["payment_outcome_inferred"] = False
        elif kind == "baseline_population_reused":
            copy_hashes(result, data, ("population_sha256",))
            copy_count(result, data, ("requested",))
            copy_enum(result, data, "initial_state", {"saved_initial_snapshot"})
            copy_bool(result, data, ("generation_performed",))
        elif kind == "discovery":
            if data.get("sdk") == "browser-use==0.13.11":
                result["sdk"] = data["sdk"]
            if data.get("measured_payment") is False:
                result["measured_payment"] = False
            list_count(result, data, "actions", "action_count")
        elif kind in ("persona_initialized", "persona_memory", "decision"):
            copy_count(result, data, ("memory_count", "episodic_count"))
            list_count(result, data, "memory", "episodic_count")
            list_count(result, data, "semantic_memory", "semantic_count")
            if data.get("sdk") == "tinytroupe==0.7.0":
                result["sdk"] = data["sdk"]
            intent = mapping(data.get("intent"))
            if isinstance(intent.get("type"), str) and intent["type"] in ("BROWSER", "FINISH", "ABANDON"):
                result["intent_type"] = intent["type"]
            choice = intent.get("target")
            if isinstance(choice, str) and re.fullmatch(r"[0-9]{1,2}:[0-9]{1,4}", choice):
                result["action_choice"] = choice
            copy_enum(result, data, "source", {
                "saved_persona_conditional_plan", "disclosed_deterministic_guarded_plan_execution",
                "actual_tinyperson_unexpected_state_decision",
            })
            counters = {}
            copy_count(counters, mapping(data.get("cost_stats")), (
                "input_tokens", "output_tokens", "total_tokens", "model_calls", "cached_calls",
            ))
            if counters:
                result["cost_stats"] = counters
        elif kind == "scenario_fixture":
            browser = mapping(data.get("browser"))
            copy_bool(result, browser, ("fresh_context", "has_touch"))
            copy_enum(result, browser, "locale", {"en-US"})
            copy_count(result, mapping(browser.get("viewport")), ("width", "height"))
            manifest = mapping(data.get("source_manifest"))
            copy_bool(result, manifest, ("source_clean",))
            copy_enum(result, manifest, "mode", {"healthy", "broken"})
            copy_hashes(result, manifest, ("source_sha256",))
        elif kind == "observation":
            list_count(result, data, "actions", "action_count")
            copy_bool(result, data, ("capability_observation",))
            digest = self.persisted_hash(data.get("screenshot"))
            if digest:
                result["persisted_screenshot_sha256"] = digest
        elif kind in ("browser_action", "known_replay_action", "known_replay_capability"):
            action = mapping(data.get("action"))
            copy_enum(result, action, "method", {"click", "fill", "selectOptionFromDropdown"})
            execution = mapping(data.get("result"))
            copy_bool(result, execution, ("success", "field_completed"))
            copy_bool(result, mapping(execution.get("data")), ("success",))
            capability = mapping(execution.get("capability"))
            copy_bool(result, capability, ("available",))
            copy_count(result, capability, ("bytes",))
            copy_hashes(result, capability, ("download_sha256",))
        elif kind == "scenario_payment_condition":
            observed = mapping(data.get("observation"))
            copy_enum(result, observed, "billing_country", {"US"})
            copy_bool(result, observed, ("accepted_native_confirm",))
            list_count(result, observed, "completed_fields", "completed_field_count")
        elif kind == "capability":
            copy_bool(result, data, ("available",))
            copy_count(result, data, ("bytes",))
            copy_hashes(result, data, ("download_sha256",))
        elif kind == "repair":
            if isinstance(data.get("repair_id"), str) and HEX_ID.fullmatch(data["repair_id"]):
                result["repair_id"] = data["repair_id"]
            for key in ("base_revision", "head_revision", "target_revision"):
                if isinstance(data.get(key), str) and REVISION.fullmatch(data[key]):
                    result[key] = data[key]
            copy_hashes(result, data, ("source_digest",))
            checkpoint = data.get("checkpoint_id")
            if isinstance(checkpoint, str) and re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", checkpoint):
                result["checkpoint_id"] = checkpoint
        elif kind == "connection_recovery":
            copy_enum(result, data, "kind", {"connection_only_preview", "unpaid_fixture_diagnostic"})
            copy_enum(result, data, "mode", {"verify_existing_checkpoint_only"})
            copy_count(result, data, (
                "diagnostic_allowance_already_consumed_microusd",
                "additional_estimated_consumption_microusd", "measured_cash_microusd",
            ))
            list_count(result, data, "prior_gates", "prior_gate_count")
        elif kind == "known_replay_reused":
            for key in ("source_run_id", "trace_id"):
                if isinstance(data.get(key), str) and HEX_ID.fullmatch(data[key]):
                    result[key] = data[key]
            copy_bool(result, data, ("reused", "new_diagnostic_execution", "not_in_population_denominator"))
            list_count(result, data, "original_events", "original_event_count")
            original = []
            for item in data.get("original_events", []) if isinstance(data.get("original_events"), list) else []:
                item = mapping(item)
                safe = {}
                copy_count(safe, item, ("event_id",))
                copy_hashes(safe, item, ("payload_sha256",))
                copy_enum(safe, item, "kind", {"known_replay_capability", "known_failure_replay", "held_out_checks"})
                if safe:
                    original.append(safe)
            result["original_events"] = original[:3]
        elif kind == "held_out_checks":
            cases = []
            allowed = {"unpaid_access_denied", "duplicate_payment_grants_once", "healthy_revision_control",
                       "duplicate_reconciliation_grants_once"}
            for item in data.get("cases", []) if isinstance(data.get("cases"), list) else []:
                case = mapping(item)
                if isinstance(case.get("case"), str) and case["case"] in allowed:
                    safe = {"case": case["case"]}
                    copy_bool(safe, case, ("passed", "measured", "not_in_population_denominator"))
                    cases.append(safe)
            result["cases"] = cases
            copy_bool(result, data, ("no_additional_provider_checkout",))
            copy_bool(result, data, ("reused", "not_in_population_denominator"))
            if isinstance(data.get("source_run_id"), str) and HEX_ID.fullmatch(data["source_run_id"]):
                result["source_run_id"] = data["source_run_id"]
        if kind in {"known_replay_reused", "held_out_checks"}:
            recorded_at = data.get("recorded_at")
            if isinstance(recorded_at, str) and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", recorded_at):
                result["recorded_at"] = recorded_at
        if kind in ("verification", "known_failure_replay"):
            for key, allowed in (("outcome", OUTCOMES), ("classification", CLASSIFICATIONS)):
                copy_enum(result, data, key, allowed)
            if data.get("provider") == self.context.get("provider"):
                result["provider"] = data["provider"]
            copy_bool(result, data, BOOLS)
            oracle = {}
            for stage in ("orders_after", "provider_receipt", "app_entitlement"):
                observed = mapping(mapping(data.get("oracle_http")).get(stage))
                facts = {}
                status = observed.get("http_status")
                if type(status) is int and 100 <= status <= 599:
                    facts["http_status"] = status
                if observed.get("method") == "GET":
                    facts["method"] = "GET"
                copy_count(facts, observed, ("redirects_followed",))
                if facts:
                    oracle[stage] = facts
            if oracle:
                result["oracle_http"] = oracle
            entitlement = mapping(data.get("app_entitlement"))
            copy_bool(result, entitlement, ("has_access", "order_has_grant", "active"))
            copy_count(result, entitlement, ("grant_count", "account_grant_count"))
        return result

    def mask(self, snapshot):
        name = snapshot.get("name")
        original = mapping(snapshot.get("attributes"))
        if original.get("tryfolk.scope_rejected"):
            return None
        if name == "neatlogs.trace.complete":
            if not (original.get("neatlogs.trace.complete") is True
                    and original.get("neatlogs.internal") is True
                    and original.get("neatlogs.span.kind") == "Neatlogs.INTERNAL"):
                self.reject("invalid_completion_marker")
                return None
            attributes = {
                "neatlogs.trace.complete": True, "neatlogs.internal": True,
                "neatlogs.span.kind": "Neatlogs.INTERNAL",
            }
        elif name in {"tryfolk.persona_run", "tryfolk.persona_journey"} or name in {"tryfolk." + kind for kind in KINDS}:
            incoming = json_mapping(original.get("input.value"))
            identity = dict(self.context)
            persona = incoming.get("persona_id")
            if isinstance(persona, str) and HEX_ID.fullmatch(persona):
                identity["persona_id"] = persona
            kind = name.removeprefix("tryfolk.")
            data = json_mapping(original.get("output.value"))
            if kind == "conditional_purchase_plan":
                plan = mapping(data.get("plan"))
                with self._lock:
                    bound = self._persona_intents.get(identity.get("persona_id"))
                bound_intent = bound == (plan.get("profile_sha256"), plan.get("scenario_sha256"))
                if (
                    self.context.get("provider") != "dodo" or not identity.get("persona_id")
                    or plan.get("persona_id") != identity["persona_id"]
                    or (not bound_intent and plan.get("scenario_sha256") not in self._scenario_hashes)
                    or (bound is not None and not bound_intent)
                    or not isinstance(plan.get("profile_sha256"), str) or not HASH.fullmatch(plan["profile_sha256"])
                    or plan.get("source") != "genuine_codex_batch_intent" or type(plan.get("purchase")) is not bool
                    or type(plan.get("max_steps")) is not int or not 1 <= plan["max_steps"] <= 48
                    or plan.get("unexpected_state") not in {"reconsider", "abandon"}
                    or data.get("payment_outcome_inferred") is not False
                ):
                    self.reject("conditional_plan_binding")
                    return None
            if kind == "baseline_population_reused" and (
                not self._baseline_reuse or type(data.get("requested")) is not int
                or data.get("generation_performed") is not False
                or any(data.get(key) != value for key, value in self._baseline_reuse.items())
            ):
                self.reject("baseline_population_binding")
                return None
            summary = self.summary(kind, data, identity.get("persona_id"))
            if kind != "persona_run" and isinstance(original.get("output.value"), str):
                summary["observed_payload_sha256"] = hashlib.sha256(
                    original["output.value"].encode()
                ).hexdigest()
            attributes = {
                "openinference.span.kind": "AGENT" if kind in {"persona_run", "persona_journey"} else "TOOL"
                if kind in {"browser_action", "verification", "known_replay_action", "known_replay_capability"}
                else "CHAIN",
                "input.value": json.dumps(identity, sort_keys=True),
                "input.mime_type": "application/json",
                "output.value": json.dumps(summary, sort_keys=True),
                "output.mime_type": "application/json",
            }
        else:
            self.reject("unknown_span")
            return None
        status = mapping(snapshot.get("status"))
        code = status.get("code") if status.get("code") in {"ERROR", "OK", "UNSET"} else "UNSET"
        description = status.get("description")
        events = []
        for event in snapshot.get("events", []):
            if mapping(event).get("name") == "exception":
                events.append({"name": "exception", "timestamp": event.get("timestamp"), "attributes": {}})
        links = [dict(link, attributes={}) for link in snapshot.get("links", [])]
        return {
            "name": name, "attributes": attributes, "events": events, "links": links,
            "resource": {"attributes": {"service.name": WORKFLOW, "neatlogs.workflow_name": WORKFLOW}},
            "status": {"code": code, "description": description if description in CLASSIFICATIONS else None},
        }
