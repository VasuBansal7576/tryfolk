"""Actual Neatlogs OTLP spans; delivery and finalized readback are distinct."""

from contextlib import contextmanager
import json
import os
from pathlib import Path


class Telemetry:
    def __init__(self):
        self.client = None
        self.trace_id = None
        self.key = os.environ.get("NEATLOGS_API_KEY")

    @contextmanager
    def run(self, run):
        if self.key:
            from neatlogs import Client

            self.client = Client(
                api_key=self.key,
                workflow_name="tryfolk-local-runtime",
                capture_logs=False,
                uploads_enabled=False,
            )
        if not self.client:
            yield
            return
        try:
            with self.client.activate():
                with self.client.get_tracer("tryfolk").start_as_current_span(
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
                    self.trace_id = f"{span.get_span_context().trace_id:032x}"
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
            self.client.shutdown()
            path = (
                Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
                / f"{run['id']}-{self.trace_id}-trace.json"
            )
            path.write_text(
                json.dumps(
                    {
                        "trace_id": self.trace_id,
                        "delivery_flushed": delivered,
                        "finalized_readback": "not_verified",
                        "readback_url": self.url,
                    },
                    indent=2,
                )
            )
            self.client = None

    @property
    def url(self):
        return "https://ingest.neatlogs.com/api/traces/v3/" + self.trace_id if self.trace_id else None

    def event(self, kind, data, persona_id=None):
        if not self.client:
            return
        with self.client.get_tracer("tryfolk").start_as_current_span(
            "tryfolk." + kind,
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
