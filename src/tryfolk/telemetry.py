"""Actual Neatlogs OTLP spans; delivery and finalized readback are distinct."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
from .trace_policy import BoundaryProcessors, ENDPOINT, SCOPE, TracePolicy


class Telemetry:
    def __init__(self):
        self.client = None
        self.trace_id = None
        self.root_context = None
        self.key = os.environ.get("NEATLOGS_API_KEY")

    @contextmanager
    def run(self, run):
        policy = TracePolicy(run, os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
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
            yield
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

                    self.trace_id = f"{span.get_span_context().trace_id:032x}"
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
            path = (
                Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
                / f"{run['id']}-{self.trace_id}-trace.json"
            )
            path.write_text(
                json.dumps(
                    {
                        "trace_id": self.trace_id,
                        "delivery_flushed": bool(delivered and shutdown and not policy.gates),
                        "transport_flushed": delivered,
                        "payload_policy": policy.context["policy"],
                        "payload_gates": policy.gates,
                        "delivery_counters": counters,
                        "finalized_readback": "not_verified",
                        "readback_url": self.url,
                    },
                    indent=2,
                )
            )
            self.client = None
            self.root_context = None

    @property
    def url(self):
        return "https://ingest.neatlogs.com/api/traces/v3/" + self.trace_id if self.trace_id else None

    def event(self, kind, data, persona_id=None):
        if not self.client:
            return
        with self.client.get_tracer(SCOPE).start_as_current_span(
            "tryfolk." + kind,
            context=self.root_context,
            attributes={
                "openinference.span.kind": "TOOL" if kind in ("browser_action", "verification") else "CHAIN",
                "input.value": json.dumps({"persona_id": persona_id, "kind": kind}),
                "input.mime_type": "application/json",
                "output.value": json.dumps(data, default=str),
                "output.mime_type": "application/json",
            },
        ) as span:
            if kind == "verification" and data.get("outcome") == "failed":
                from opentelemetry.trace import Status, StatusCode

                span.set_status(
                    Status(StatusCode.ERROR, data.get("classification", "Verified fixture defect"))
                )
