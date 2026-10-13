"""Actual Neatlogs OTLP spans; delivery and finalized readback are distinct."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from .trace_policy import BoundaryProcessors, ENDPOINT, HEX_ID, SCOPE, TracePolicy


def neatlogs_key():
    key = os.environ.get("NEATLOGS_API_KEY")
    if key:
        return key
    path = os.environ.get("TRYFOLK_NEATLOGS_ENV")
    if path:
        from dotenv import dotenv_values

        return dotenv_values(path).get("NEATLOGS_API_KEY")
    return None


def viewer_url(trace_id):
    url = "https://app.neatlogs.com/traces/" + trace_id
    fields = {
        key: value for key, value in (
            ("orgId", os.environ.get("TRYFOLK_NEATLOGS_ORG_ID")),
            ("projectId", os.environ.get("TRYFOLK_NEATLOGS_PROJECT_ID")),
        ) if value
    }
    return url + ("?" + urlencode(fields) if fields else "")


async def read_trace(run_id, trace_id):
    """Read an existing owned trace. HTTP acceptance alone never verifies delivery."""
    if not all(isinstance(value, str) and HEX_ID.fullmatch(value) for value in (run_id, trace_id)):
        raise ValueError("Invalid trace identity")
    evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
    path = evidence / f"{run_id}-{trace_id}-trace.json"
    if not path.is_file():
        return {"trace_id": trace_id, "finalized_readback": "not_verified", "reason": "local_receipt_pending"}
    receipt = json.loads(path.read_text())
    result = {
        "trace_id": trace_id, "run_id": run_id, "persona_id": receipt.get("persona_id"),
        "pass": receipt.get("pass"), "viewer_url": receipt.get("viewer_url"),
        "delivery_flushed": receipt.get("delivery_flushed") is True,
        "finalized_readback": "not_verified", "source": "official_neatlogs_trace_api", "spans": [],
    }
    key = neatlogs_key()
    if not key:
        result["reason"] = "credential_missing"
        return result
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            async with client.stream(
                "GET", "https://ingest.neatlogs.com/api/traces/v3/" + trace_id,
                headers={"x-api-key": key},
            ) as response:
                result["http_status"] = response.status_code
                if response.status_code != 200:
                    result["reason"] = (
                        "authentication_failed" if response.status_code in {401, 403}
                        else "processing" if response.status_code in {202, 404}
                        else "readback_unavailable"
                    )
                    return result
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 4_000_000:
                        result["reason"] = "readback_too_large"
                        return result
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Invalid readback object")
    except (httpx.HTTPError, ValueError):
        result["reason"] = "readback_unavailable"
        return result

    spans = payload.get("spans", [])
    if not isinstance(spans, list) or any(not isinstance(span, dict) for span in spans):
        result["reason"] = "readback_invalid"
        return result
    actual = {}
    duplicate = False
    for span in spans:
        name = span.get("node_name") or span.get("span_name")
        if name == "neatlogs.trace.complete":
            continue
        span_id = span.get("span_id")
        if not isinstance(span_id, str):
            result["reason"] = "readback_invalid"
            return result
        duplicate = duplicate or span_id in actual
        actual[span_id] = name
    expected = receipt.get("expected_spans", {})
    # Hosted error describes the agent's outcome, not an incomplete trace.
    # A failed checkout remains valid evidence when every emitted span is present.
    result["hosted_status"] = payload.get("status")
    verified = bool(
        result["delivery_flushed"] and expected and not duplicate
        and payload.get("_id") == trace_id
        and isinstance(payload.get("status"), str) and payload["status"] in {"success", "error"}
        and actual == expected
    )
    result["reason"] = "trace_finalized_and_matched" if verified else "trace_not_finalized_or_mismatched"
    if verified:
        result["finalized_readback"] = "verified"
        # These values are the actual hosted materialization of the closed outbound summaries.
        result["spans"] = [{
            "span_id": span["span_id"], "parent_span_id": span.get("parent_span_id"),
            "name": span.get("node_name") or span.get("span_name"),
            "data": span.get("data", {}),
        } for span in spans if span.get("span_id") in expected]
        raw_path = evidence / f"{run_id}-{trace_id}-readback.json"
        raw_path.write_text(json.dumps(payload, indent=2))
    attempt = {key: value for key, value in result.items() if key != "spans"}
    attempt["checked_at"] = datetime.now(timezone.utc).isoformat()
    receipt.setdefault("readback_attempts", []).append(attempt)
    # Preserve a verified readback if a later read encounters transient service failure.
    if verified:
        receipt["finalized_readback"] = "verified"
        receipt["hosted_status"] = result["hosted_status"]
    path.write_text(json.dumps(receipt, indent=2))
    return result


class Telemetry:
    def __init__(self):
        self.client = None
        self.trace_id = None
        self.root_context = None
        self.key = neatlogs_key()
        self.persona_contexts = {}
        self.persona_traces = {}
        self.expected_spans = {}
        self.policy = None

    def bind_purchase_persona(self, persona):
        if self.policy is not None:
            self.policy.bind_purchase_persona(persona)

    def _remember(self, span, name):
        context = span.get_span_context()
        trace_id = f"{context.trace_id:032x}"
        self.expected_spans.setdefault(trace_id, {})[f"{context.span_id:016x}"] = name
        return trace_id

    @contextmanager
    def run(self, run):
        self.trace_id = None
        self.persona_contexts = {}
        self.persona_traces = {}
        self.expected_spans = {}
        policy = TracePolicy(run, os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
        self.policy = policy
        if self.key:
            from neatlogs import Client
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased

            provider = TracerProvider(
                active_span_processor=BoundaryProcessors(policy),
                sampler=ParentBased(ALWAYS_ON), shutdown_on_exit=False,
            )

            self.client = Client(
                api_key=self.key,
                workflow_name="tryfolk-local-runtime",
                capture_logs=False,
                uploads_enabled=False,
                endpoint=ENDPOINT,
                mask=policy.mask,
                tracer_provider=provider,
            )
        if not self.client:
            try:
                yield
            finally:
                self.policy = None
            return
        try:
            with self.client.activate():
                with self.client.get_tracer(SCOPE).start_as_current_span(
                    "tryfolk.persona_run",
                    attributes={
                        "openinference.span.kind": "AGENT",
                        "input.value": json.dumps(
                            {
                                "run_id": run["id"],
                                "N": run["persona_count"],
                                "revision": run["revision"],
                                "provider": run["provider"],
                            }
                        ),
                        "input.mime_type": "application/json",
                        "metadata": json.dumps(
                            {
                                "run_id": run["id"],
                                "population_id": run["population_id"],
                                "pass": run["pass"],
                                "local_fixture": run["provider"] == "local-fixture",
                            }
                        ),
                    },
                ) as span:
                    from opentelemetry.trace import set_span_in_context

                    self.trace_id = self._remember(span, "tryfolk.persona_run")
                    self.root_context = set_span_in_context(span)
                    yield
                    span.set_attribute(
                        "output.value",
                        json.dumps(
                            {
                                "run_id": run["id"],
                                "trace_id": self.trace_id,
                                "result": "See correlated verification spans; no Dodo claim for fixture.",
                            }
                        ),
                    )
        finally:
            delivered = self.client.flush()
            shutdown = self.client.shutdown()
            diagnostics = self.client.get_delivery_diagnostics()
            counters = {}
            for counter in (
                "masked_span_drops", "span_queue_drops", "span_export_failures",
                "span_capture_truncations", "span_overflow_failures", "span_overflow_unavailable",
                "span_media_upload_failures",
            ):
                value = diagnostics.get(counter)
                if type(value) is int and value >= 0:
                    counters[counter] = value
                    if value:
                        policy.reject(counter)
            provider.shutdown()
            evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
            evidence.mkdir(parents=True, exist_ok=True)
            for persona_id, trace_id in [(None, self.trace_id), *self.persona_traces.items()]:
                path = evidence / f"{run['id']}-{trace_id}-trace.json"
                path.write_text(json.dumps({
                        "trace_id": trace_id,
                        "run_id": run["id"],
                        "persona_id": persona_id,
                        "pass": run["pass"],
                        "diagnostic": run.get("diagnostic") is True,
                        "not_in_population_denominator": run.get("not_in_population_denominator") is True,
                        "delivery_flushed": bool(delivered and shutdown and not policy.gates),
                        "transport_flushed": delivered,
                        "payload_policy": policy.context["policy"],
                        "payload_gates": policy.gates,
                        "delivery_counters": counters,
                        "finalized_readback": "not_verified",
                        "readback_url": "https://ingest.neatlogs.com/api/traces/v3/" + trace_id,
                        "viewer_url": viewer_url(trace_id),
                        "expected_spans": self.expected_spans.get(trace_id, {}),
                    }, indent=2))
            self.client = None
            self.root_context = None
            self.policy = None

    @property
    def url(self):
        return viewer_url(self.trace_id) if self.trace_id else None

    def persona_url(self, persona_id):
        trace_id = self.persona_traces.get(persona_id)
        return viewer_url(trace_id) if trace_id else None

    @contextmanager
    def persona(self, persona_id):
        """A separate genuine trace for one journey, safe for concurrent asyncio tasks."""
        if not self.client:
            yield
            return
        if not isinstance(persona_id, str) or not HEX_ID.fullmatch(persona_id):
            raise ValueError("Invalid persona trace identity")
        if persona_id in self.persona_contexts:
            raise RuntimeError("Persona journey trace already active")
        from opentelemetry.context import Context
        from opentelemetry.trace import set_span_in_context

        with self.client.get_tracer(SCOPE).start_as_current_span(
            "tryfolk.persona_journey", context=Context(),
            attributes={
                "openinference.span.kind": "AGENT",
                "input.value": json.dumps({"persona_id": persona_id}),
                "input.mime_type": "application/json",
            },
        ) as span:
            self.persona_traces[persona_id] = self._remember(span, "tryfolk.persona_journey")
            self.persona_contexts[persona_id] = set_span_in_context(span)
            try:
                yield
            finally:
                self.persona_contexts.pop(persona_id, None)

    def event(self, kind, data, persona_id=None):
        if not self.client:
            return
        with self.client.get_tracer(SCOPE).start_as_current_span(
            "tryfolk." + kind,
            context=self.persona_contexts.get(persona_id, self.root_context),
            attributes={
                "openinference.span.kind": "TOOL" if kind in ("browser_action", "verification") else "CHAIN",
                "input.value": json.dumps({"persona_id": persona_id, "kind": kind}),
                "input.mime_type": "application/json",
                "output.value": json.dumps(data, default=str),
                "output.mime_type": "application/json",
            },
        ) as span:
            self._remember(span, "tryfolk." + kind)
            if kind == "verification" and data.get("outcome") == "failed":
                from opentelemetry.trace import Status, StatusCode

                span.set_status(
                    Status(StatusCode.ERROR, data.get("classification", "Verified fixture defect"))
                )
