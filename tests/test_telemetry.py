"""Real installed Neatlogs -> OTLP serialization; only outbound network I/O is replaced."""

from copy import deepcopy
import gzip
import json
import os
from pathlib import Path
import socket

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
import pytest
import requests

from tryfolk.telemetry import Telemetry

POISON = "TRYFOLK_OUTBOUND_PRIVATE_CANARY"
RUN = {
    "id": "1" * 32,
    "population_id": "2" * 32,
    "persona_count": 1,
    "revision": "sha256:" + "a" * 64,
    "provider": "dodo",
    "pass": "before",
}


@pytest.fixture
def otlp(monkeypatch, tmp_path, request):
    requests_seen = []
    compressed = []
    network_attempts = []
    monkeypatch.setenv("NEATLOGS_API_KEY", "offline-test-key")
    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(tmp_path))

    def send(session, request, **kwargs):
        assert request.url == "https://ingest.neatlogs.com/v1/traces"
        assert kwargs.get("allow_redirects") is False
        assert request.method == "POST"
        assert request.headers.get("x-api-key") == "offline-test-key"
        compressed.append(request.body)
        data = request.body
        if request.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        parsed = ExportTraceServiceRequest.FromString(data)
        requests_seen.append((data, parsed))
        response = requests.Response()
        response.status_code = 200
        response._content = b""
        response.request = request
        return response

    def no_socket(*args, **kwargs):
        network_attempts.append("blocked")
        raise AssertionError("An offline telemetry test attempted a real connection")

    monkeypatch.setattr(requests.Session, "send", send)
    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", no_socket)
    yield requests_seen
    assert not network_attempts
    proof_root = os.environ.get("TRYFOLK_OFFLINE_TRACE_PROOF")
    if proof_root:
        destination = Path(proof_root) / request.node.name
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        for index, (data, parsed) in enumerate(requests_seen):
            (destination / f"{index}.otlp.gz").write_bytes(compressed[index])
            (destination / f"{index}.otlp.pb").write_bytes(data)
            (destination / f"{index}.otlp.json").write_text(json.dumps(MessageToDict(parsed), indent=2))
        receipts = [json.loads(path.read_text()) for path in tmp_path.glob("*-trace.json")]
        (destination / "result.json").write_text(json.dumps({
            "fixture": "invented, offline transport response only", "network_attempts": 0,
            "requests": len(requests_seen), "completion_markers": sum(
                span.name == "neatlogs.trace.complete" for span in spans(requests_seen)),
            "local_receipts": receipts,
        }, indent=2))
        for path in destination.iterdir():
            path.chmod(0o400)


def spans(requests_seen):
    return [
        span
        for _, request in requests_seen
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]


def attrs(span):
    return {item.key: item.value for item in span.attributes}


def test_final_verification_exports_safe_facts_and_leaves_full_local_proof_intact(otlp):
    proof = {
        "outcome": "failed",
        "classification": "paid_without_access",
        "provider": "dodo",
        "measured": True,
        "payment_verified": True,
        "entitlement_verified": False,
        "capability_checked": True,
        "capability_available": False,
        "oracle_http": {"provider_receipt": {"http_status": 200, "body": {"email": POISON}}},
        "actions": [{"arguments": [POISON], "description": POISON}],
        "receipt": {"payment_link": "https://example.invalid/" + POISON},
        "snapshot": {"memory": POISON},
        "unknown": POISON,
    }
    original = deepcopy(proof)
    telemetry = Telemetry()
    with telemetry.run(RUN):
        telemetry.event("verification", proof, "3" * 32)
    assert otlp, "Real SDK must export to the intercepted transport"
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    verification = next(span for span in spans(otlp) if span.name == "tryfolk.verification")
    summary = json.loads(attrs(verification)["output.value"].string_value)
    assert summary["payment_verified"] is True
    assert summary["entitlement_verified"] is False
    assert summary["outcome"] == "failed"
    assert proof == original


def test_registered_override_and_hidden_scope_metadata_cannot_bypass_policy(otlp):
    from neatlogs.core.mask import register_mask

    override = register_mask(lambda snapshot: snapshot)
    telemetry = Telemetry()
    with telemetry.run(RUN):
        tracer = telemetry.client.tracer_provider.get_tracer(
            "tryfolk", instrumenting_library_version=POISON, schema_url="https://example.invalid/" + POISON,
            attributes={"private": POISON},
        )
        with tracer.start_as_current_span("tryfolk.observation", attributes={
            "neatlogs.mask_id": override,
            "output.value": json.dumps({"text": POISON}),
        }):
            pass
    assert POISON.encode() not in b"".join(data for data, _ in otlp)


def test_automatic_exception_and_env_resource_export_no_private_text(otlp, monkeypatch):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "private.contact=" + POISON)
    telemetry = Telemetry()
    with pytest.raises(RuntimeError, match=POISON):
        with telemetry.run(RUN):
            raise RuntimeError(POISON)
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    root = next(span for span in spans(otlp) if span.name == "tryfolk.persona_run")
    assert root.status.code == 2  # Actual ERROR, not manufactured success.
    assert root.status.message == ""
    assert any(event.name == "exception" for event in root.events)


def test_cognition_keeps_actual_counts_and_intent_with_correlated_context(otlp):
    telemetry = Telemetry()
    with telemetry.run(RUN):
        telemetry.event("decision", {
            "intent": {"type": "BROWSER", "target": "2:4", "content": POISON},
            "memory": [{"content": POISON}, {"content": POISON}],
            "semantic_memory": [{"content": POISON}],
            "snapshot": {"private": POISON},
        }, "3" * 32)
    exported = spans(otlp)
    root = next(span for span in exported if span.name == "tryfolk.persona_run")
    decision = next(span for span in exported if span.name == "tryfolk.decision")
    summary = json.loads(attrs(decision)["output.value"].string_value)
    assert summary["intent_type"] == "BROWSER"
    assert summary["episodic_count"] == 2
    assert summary["semantic_count"] == 1
    assert decision.trace_id == root.trace_id
    assert decision.parent_span_id == root.span_id
    assert root.start_time_unix_nano <= decision.start_time_unix_nano < decision.end_time_unix_nano
    assert decision.end_time_unix_nano <= root.end_time_unix_nano
    marker = [span for span in exported if span.name == "neatlogs.trace.complete"]
    assert len(marker) == 1
    assert marker[0].trace_id == root.trace_id
    assert POISON.encode() not in b"".join(data for data, _ in otlp)


def test_recovery_and_persisted_screenshot_keep_observed_facts_only(otlp, tmp_path):
    screenshot = tmp_path / "private.png"
    screenshot.write_bytes(b"abc")
    original = screenshot.read_bytes()
    telemetry = Telemetry()
    with telemetry.run(RUN):
        telemetry.event("connection_recovery", {
            "kind": "connection_only_preview", "additional_estimated_consumption_microusd": 0,
            "measured_cash_microusd": 0, "prior_gates": [{"message": POISON}], "basis": POISON,
        })
        telemetry.event("observation", {"screenshot": str(screenshot), "text": POISON, "actions": []})
    exported = spans(otlp)
    recovery = next(span for span in exported if span.name == "tryfolk.connection_recovery")
    summary = json.loads(attrs(recovery)["output.value"].string_value)
    assert summary["kind"] == "connection_only_preview"
    assert summary["additional_estimated_consumption_microusd"] == 0
    assert summary["prior_gate_count"] == 1
    observation = next(span for span in exported if span.name == "tryfolk.observation")
    summary = json.loads(attrs(observation)["output.value"].string_value)
    assert summary["persisted_screenshot_sha256"] == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert screenshot.read_bytes() == original
    assert POISON.encode() not in b"".join(data for data, _ in otlp)


def test_foreign_binding_and_evidence_files_do_not_export_as_correlated_proof(otlp, tmp_path):
    outside = tmp_path.parent / "outside-owned-evidence.txt"
    outside.write_bytes(b"abc")
    telemetry = Telemetry()
    with telemetry.run(RUN):
        telemetry.event("scenario_fixture", {"binding": {
            "run_id": "9" * 32, "persona_id": "3" * 32, "target_revision": RUN["revision"],
            "scenario_sha256": "b" * 64,
        }}, "3" * 32)
        telemetry.event("observation", {"screenshot": str(outside), "text": POISON})
    fixture = next(span for span in spans(otlp) if span.name == "tryfolk.scenario_fixture")
    observation = next(span for span in spans(otlp) if span.name == "tryfolk.observation")
    assert "scenario_sha256" not in json.loads(attrs(fixture)["output.value"].string_value)
    assert "persisted_screenshot_sha256" not in json.loads(attrs(observation)["output.value"].string_value)
    assert outside.read_bytes() == b"abc"


def test_all_existing_stages_keep_facts_without_raw_payload_or_unknown_values(otlp):
    binding = {
        "run_id": RUN["id"], "persona_id": "3" * 32, "target_revision": RUN["revision"],
        "scenario_sha256": "b" * 64, "saved_persona_sha256": "c" * 64,
        "profile_sha256": "d" * 64, "initial_memory_sha256": "e" * 64,
        "condition_fixture_sha256": "f" * 64, "account_id": POISON,
    }
    action = {"method": "click", "arguments": [POISON], "description": POISON}
    result = {"success": True, "capability": {"available": False, "bytes": 0, "download_path": POISON}}
    verification = {
        "outcome": "failed", "classification": "paid_without_access", "measured": True,
        "payment_verified": True, "entitlement_verified": False, "actions": [action],
        "oracle_http": {"orders_after": {"http_status": 200, "method": "GET", "body": POISON}},
    }
    cases = {
        "discovery": ({"sdk": "browser-use==0.13.11", "actions": [action]}, {"action_count": 1}),
        "persona_initialized": ({"memory_count": 2}, {"memory_count": 2}),
        "scenario_fixture": ({"binding": binding, "browser": {"fresh_context": True},
                              "source_manifest": {"source_clean": True, "source_sha256": "a" * 64}},
                             {"initial_memory_sha256": "e" * 64, "source_clean": True}),
        "observation": ({"actions": [action]}, {"action_count": 1}),
        "decision": ({"intent": {"type": "BROWSER", "target": "1:2", "content": POISON}},
                     {"intent_type": "BROWSER", "action_choice": "1:2"}),
        "browser_action": ({"action": action, "result": result}, {"method": "click", "success": True}),
        "scenario_payment_condition": ({"binding": binding, "observation": {
            "completed_fields": [POISON] * 4, "billing_country": "US", "accepted_native_confirm": True}},
            {"completed_field_count": 4, "accepted_native_confirm": True, "scenario_sha256": "b" * 64}),
        "capability": ({"available": False, "bytes": 0}, {"available": False, "bytes": 0}),
        "persona_memory": ({"episodic_count": 3, "semantic_memory": [{"text": POISON}]},
                           {"episodic_count": 3, "semantic_count": 1}),
        "verification": (verification, {"payment_verified": True, "entitlement_verified": False}),
        "repair": ({"repair_id": "4" * 32, "head_revision": "b" * 40, "checkpoint_url": POISON},
                   {"repair_id": "4" * 32, "head_revision": "b" * 40}),
        "known_replay_action": ({"action": action, "result": result}, {"method": "click"}),
        "known_replay_capability": ({"action": action, "result": result}, {"available": False}),
        "known_failure_replay": ({**verification, "not_in_population_denominator": True},
                                 {"not_in_population_denominator": True, "payment_verified": True}),
        "held_out_checks": ({"cases": [{"case": "unpaid_access_denied", "passed": True, "raw": POISON}]},
                            {"cases": [{"case": "unpaid_access_denied", "passed": True}]}),
        "connection_recovery": ({"kind": "unpaid_fixture_diagnostic"}, {"kind": "unpaid_fixture_diagnostic"}),
    }
    original = deepcopy(cases)
    telemetry = Telemetry()
    with telemetry.run({**RUN, "pass": "after"}):
        for kind, (data, _) in cases.items():
            telemetry.event(kind, {**data, "unknown": POISON}, "3" * 32)
        telemetry.event("capability", {"available": POISON, "bytes": True, "download_sha256": POISON})
    exported = spans(otlp)
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    for kind, (_, expected) in cases.items():
        span = next(span for span in exported if span.name == "tryfolk." + kind)
        summary = json.loads(attrs(span)["output.value"].string_value)
        assert {key: summary[key] for key in expected} == expected
        assert len(summary["observed_payload_sha256"]) == 64
        assert json.loads(attrs(span)["input.value"].string_value)["pass"] == "after"
    malformed = [span for span in exported if span.name == "tryfolk.capability"][-1]
    assert set(json.loads(attrs(malformed)["output.value"].string_value)) == {"observed_payload_sha256"}
    assert cases == original


def test_dropped_unknown_spans_fail_receipt_even_when_transport_flushes(otlp, tmp_path):
    telemetry = Telemetry()
    with telemetry.run(RUN):
        telemetry.event(POISON, {"private": POISON})
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    receipt = json.loads(next(tmp_path.glob("*-trace.json")).read_text())
    assert receipt["transport_flushed"] is True
    assert receipt["delivery_flushed"] is False
    assert receipt["payload_gates"] == ["masked_span_drops", "unknown_span"]
    assert receipt["finalized_readback"] == "not_verified"


def test_hidden_resource_schema_links_and_backfill_preserve_actual_context_without_private_text(otlp):
    from neatlogs import Client
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceState, set_span_in_context
    from tryfolk.trace_policy import BoundaryProcessors, ENDPOINT, SCOPE, TracePolicy

    policy = TracePolicy(RUN)
    provider = TracerProvider(
        active_span_processor=BoundaryProcessors(policy), shutdown_on_exit=False,
        resource=Resource({"private": POISON}, schema_url="https://example.invalid/" + POISON),
    )
    client = Client(api_key="offline-test-key", workflow_name="tryfolk-local-runtime", capture_logs=False,
                    uploads_enabled=False, endpoint=ENDPOINT, mask=policy.mask, tracer_provider=provider)
    with client.activate():
        with client.get_tracer(SCOPE).start_as_current_span("tryfolk.persona_run") as root:
            root_context = root.get_span_context()
            linked = SpanContext(root_context.trace_id, root_context.span_id, False,
                                 root_context.trace_flags, TraceState([("private", POISON)]))
            tracer = provider.get_tracer("tryfolk", instrumenting_library_version=POISON,
                                         schema_url="https://example.invalid/" + POISON,
                                         attributes={"private": POISON})
            with tracer.start_as_current_span(
                "tryfolk.observation", context=set_span_in_context(root),
                links=[Link(linked, attributes={"private": POISON})],
                attributes={"input.value": POISON, "output.value": json.dumps({"text": POISON})},
            ) as child:
                child.set_status(Status(StatusCode.ERROR, POISON))
                child.add_event("exception", {"exception.message": POISON, "exception.stacktrace": POISON})
            actual = (child.get_span_context(), child.start_time, child.end_time)
    assert client.flush() is True
    assert client.shutdown() is True
    provider.shutdown()
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    exported = spans(otlp)
    observed = next(span for span in exported if span.name == "tryfolk.observation")
    assert int.from_bytes(observed.trace_id) == actual[0].trace_id
    assert int.from_bytes(observed.span_id) == actual[0].span_id
    assert int.from_bytes(observed.parent_span_id) == root_context.span_id
    assert observed.start_time_unix_nano == actual[1]
    assert observed.end_time_unix_nano == actual[2]
    assert observed.status.code == 2 and observed.status.message == ""
    assert observed.links[0].trace_id == observed.trace_id
    assert int.from_bytes(observed.links[0].span_id) == root_context.span_id
    assert observed.links[0].trace_state == ""
    assert observed.events[0].name == "exception" and observed.events[0].time_unix_nano > 0
    assert len([span for span in exported if span.name == "neatlogs.trace.complete"]) == 1
    for _, request in otlp:
        for resource in request.resource_spans:
            assert resource.schema_url == ""
            for scope in resource.scope_spans:
                assert scope.schema_url == "" and scope.scope.version == "" and not scope.scope.attributes


@pytest.mark.parametrize("failure", ["drop", "raise", "nonmapping"])
def test_real_sdk_callback_failures_have_no_marker_despite_successful_flush(otlp, failure):
    from neatlogs import Client
    from opentelemetry.sdk.trace import TracerProvider
    from tryfolk.trace_policy import ENDPOINT, SCOPE

    def mask(snapshot):
        if failure == "raise":
            raise ValueError("Invented offline callback failure")
        return None if failure == "drop" else "not a mapping"

    provider = TracerProvider(shutdown_on_exit=False)
    client = Client(api_key="offline-test-key", workflow_name="tryfolk-local-runtime", capture_logs=False,
                    uploads_enabled=False, endpoint=ENDPOINT, mask=mask, tracer_provider=provider)
    with client.activate():
        with client.get_tracer(SCOPE).start_as_current_span("tryfolk.persona_run"):
            pass
    assert client.flush() is True
    assert client.shutdown() is True
    provider.shutdown()
    assert client.get_delivery_diagnostics()["masked_span_drops"] == 1
    assert not otlp  # No bytes, no completion marker, no delivery claim.


def test_export_failure_is_a_receipt_gate_even_if_flush_returns_true(otlp, monkeypatch, tmp_path):
    send = requests.Session.send

    def reject(session, request, **kwargs):
        response = send(session, request, **kwargs)
        response.status_code = 400  # No SDK retry; one invented transport rejection.
        return response

    monkeypatch.setattr(requests.Session, "send", reject)
    telemetry = Telemetry()
    with telemetry.run(RUN):
        telemetry.event("observation", {"actions": []})
    receipt = json.loads(next(tmp_path.glob("*-trace.json")).read_text())
    assert receipt["delivery_flushed"] is False
    assert "span_export_failures" in receipt["payload_gates"]
    assert receipt["finalized_readback"] == "not_verified"


def baseline_reuse():
    lineage = {"original_run_id": "4" * 32, "retry_run_id": RUN["id"],
               "population_id": RUN["population_id"], "population_sha256": "b" * 64}
    run = {**RUN, "retry_of_run_id": lineage["original_run_id"], "baseline_retry": lineage}
    event = {k: lineage[k] for k in ("original_run_id", "population_id", "population_sha256")}
    event.update(requested=1, initial_state="saved_initial_snapshot", generation_performed=False)
    return run, event


def test_real_sdk_exports_only_exact_lineage_bound_population_reuse_without_masked_drop(otlp, tmp_path):
    run, event = baseline_reuse()
    event.update(private=POISON)
    original = deepcopy(event)
    telemetry = Telemetry()
    with telemetry.run(run):
        telemetry.event("baseline_population_reused", event)
    reused = next(s for s in spans(otlp) if s.name == "tryfolk.baseline_population_reused")
    summary = json.loads(attrs(reused)["output.value"].string_value)
    assert {k: summary[k] for k in ("population_sha256", "requested", "initial_state", "generation_performed")} == {
        "population_sha256": "b" * 64, "requested": 1,
        "initial_state": "saved_initial_snapshot", "generation_performed": False,
    }
    assert "original_run_id" not in summary and "population_id" not in summary
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    receipt = json.loads(next(tmp_path.glob("*-trace.json")).read_text())
    assert receipt["delivery_flushed"] is True and receipt["payload_gates"] == []
    assert receipt["delivery_counters"]["masked_span_drops"] == 0
    assert event == original


@pytest.mark.parametrize("field,value", [
    ("original_run_id", "5" * 32), ("population_id", "6" * 32),
    ("population_sha256", "c" * 64), ("requested", 2), ("requested", True),
    ("initial_state", "fresh_generation"), ("generation_performed", True),
    ("generation_performed", 0),
])
def test_real_sdk_drops_foreign_population_reuse_binding(otlp, tmp_path, field, value):
    run, event = baseline_reuse()
    event[field] = value
    telemetry = Telemetry()
    with telemetry.run(run):
        telemetry.event("baseline_population_reused", event)
    assert not any(s.name == "tryfolk.baseline_population_reused" for s in spans(otlp))
    receipt = json.loads(next(tmp_path.glob("*-trace.json")).read_text())
    assert receipt["delivery_flushed"] is False
    assert "baseline_population_binding" in receipt["payload_gates"]
    assert receipt["delivery_counters"]["masked_span_drops"] == 1


@pytest.mark.parametrize("field,value", [
    ("retry_of_run_id", "5" * 32), ("id", None), ("population_id", None),
    ("pass", "after"), ("baseline_retry", {}),
])
def test_real_sdk_requires_valid_before_retry_lineage_for_population_reuse(otlp, tmp_path, field, value):
    run, event = baseline_reuse()
    run[field] = value
    telemetry = Telemetry()
    with telemetry.run(run):
        telemetry.event("baseline_population_reused", event)
    assert not any(s.name == "tryfolk.baseline_population_reused" for s in spans(otlp))
    receipt = json.loads(next(tmp_path.glob("*-trace.json")).read_text())
    assert receipt["delivery_flushed"] is False
    assert "baseline_population_binding" in receipt["payload_gates"]


def conditional_plan_event():
    plan = {"persona_id": "3" * 32, "profile_sha256": "c" * 64, "scenario_sha256": "d" * 64,
            "purchase": True, "rationale": POISON, "max_steps": 24, "unexpected_state": "reconsider",
            "source": "genuine_codex_batch_intent"}
    run = {**RUN, "scenario_plan": {"assignments": [{"scenario_sha256": "d" * 64}]}}
    event = {"plan": plan, "execution": POISON, "payment_outcome_inferred": False}
    return run, event


def test_real_sdk_exports_closed_conditional_plan_without_rationale_or_payment_claim(otlp, tmp_path):
    run, event = conditional_plan_event()
    telemetry = Telemetry()
    with telemetry.run(run):
        telemetry.event("conditional_purchase_plan", event, "3" * 32)
    exported = next(s for s in spans(otlp) if s.name == "tryfolk.conditional_purchase_plan")
    summary = json.loads(attrs(exported)["output.value"].string_value)
    assert summary["purchase"] is True and summary["payment_outcome_inferred"] is False
    assert summary["max_steps"] == 24 and summary["unexpected_state"] == "reconsider"
    assert summary["profile_sha256"] == "c" * 64 and summary["scenario_sha256"] == "d" * 64
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    receipt = json.loads(next(tmp_path.glob("*-trace.json")).read_text())
    assert receipt["delivery_flushed"] is True and receipt["payload_gates"] == []


@pytest.mark.parametrize("field,value", [
    ("persona_id", "9" * 32), ("scenario_sha256", "e" * 64), ("profile_sha256", "invalid"),
    ("purchase", 1), ("max_steps", True), ("max_steps", 49), ("source", "invented_fixture"),
])
def test_real_sdk_rejects_unbound_or_invalid_conditional_plan(otlp, tmp_path, field, value):
    run, event = conditional_plan_event()
    event["plan"][field] = value
    telemetry = Telemetry()
    with telemetry.run(run):
        telemetry.event("conditional_purchase_plan", event, "3" * 32)
    assert not any(s.name == "tryfolk.conditional_purchase_plan" for s in spans(otlp))
    receipt = json.loads(next(tmp_path.glob("*-trace.json")).read_text())
    assert receipt["delivery_flushed"] is False and "conditional_plan_binding" in receipt["payload_gates"]
