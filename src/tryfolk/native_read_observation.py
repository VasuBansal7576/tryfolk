"""Diagnostic native control reads. This module cannot execute browser actions."""

import asyncio
import time

OPERATIONS = frozenset(
    {
        "stagehand_snapshot",
        "stagehand_page_url",
        "parent_frame_element",
        "child_frame_lookup",
        "child_control_state",
        "stagehand_visibility",
        "stagehand_input_value",
        "child_generation",
    }
)
NATIVE_CHECKS = (
    "native_configuration_current",
    "native_generation_current",
    "independent_Test_identity_current",
    "bootstrap_readiness_current",
)


class NativeObservationGate(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("Current native read-only observation is unestablished: " + reason)


class NativeControlMissing(RuntimeError):
    """An approved read-only SDK locator lost its current iframe candidate."""


class NativeControlReads:
    """Retain static operation and duration, never selector/URL/input values."""

    def __init__(self, on_event=None):
        self.on_event = on_event or (lambda event: None)
        self.started = self.operation_started = time.monotonic()
        self.operation = None

    async def read(self, operation, callback):
        if operation not in OPERATIONS:
            raise ValueError("Unknown native read operation")
        self.operation, self.operation_started = operation, time.monotonic()
        self.on_event({"operation": operation, "phase": "start"})
        result = await callback()
        self.on_event(
            {
                "operation": operation,
                "phase": "completed",
                "elapsed_ms": round((time.monotonic() - self.operation_started) * 1000),
            }
        )
        return result

    def timeout_evidence(self):
        return {
            "operation": self.operation,
            "phase": "timeout",
            "elapsed_ms": round((time.monotonic() - self.operation_started) * 1000),
            "total_elapsed_ms": round((time.monotonic() - self.started) * 1000),
        }


async def read_current_native_controls(
    read_controls, recheck, *, deadline, on_event, max_attempts=2, stop_on_change=False
):
    """Discard every candidate on timeout/change; share the caller's deadline.

    recheck privately returns a binding plus independently observed boolean
    checks. Only the checks leave this function. No action/model capability is
    accepted here; successful candidates still require their usual final bind.
    """
    if max_attempts not in (1, 2):
        raise ValueError("Native diagnostic reads permit at most two attempts")
    loop = asyncio.get_running_loop()
    discarded_binding = None
    for attempt in range(1, max_attempts + 1):
        if loop.time() >= deadline:
            raise NativeObservationGate("original_deadline_exhausted")
        try:
            async with asyncio.timeout_at(deadline):
                before = await recheck()
                checks = before.get("checks", {})
                if not all(checks.get(key) is True for key in NATIVE_CHECKS):
                    on_event(
                        {
                            "decision": "deny_current_native_state",
                            "attempt": attempt,
                            "checks": {key: checks.get(key) is True for key in NATIVE_CHECKS},
                        }
                    )
                    raise NativeObservationGate("current_native_identity_or_generation")
                if stop_on_change and discarded_binding is not None and before.get("binding") != discarded_binding:
                    raise NativeObservationGate("native_changed_after_executed_card")
                candidates = await read_controls()
                after = await recheck()
                after_checks = after.get("checks", {})
                if (
                    not all(after_checks.get(key) is True for key in NATIVE_CHECKS)
                    or before.get("binding") != after.get("binding")
                    or not before.get("binding")
                ):
                    on_event({"decision": "discard_changed_native_state", "attempt": attempt})
                    if stop_on_change:
                        raise NativeObservationGate("native_changed_after_executed_card")
                    continue
                return candidates
        except (TimeoutError, NativeControlMissing) as error:
            private = getattr(error, "control_observation", {})
            operation = private.get("operation")
            event = {
                "decision": "discard_read_missing" if isinstance(error, NativeControlMissing) else "discard_read_timeout",
                "attempt": attempt,
                "operation": operation if operation in OPERATIONS else "unretained",
                "original_deadline_available": loop.time() < deadline,
            }
            elapsed = private.get("elapsed_ms")
            if isinstance(elapsed, int) and not isinstance(elapsed, bool) and elapsed >= 0:
                event["elapsed_ms"] = elapsed
            on_event(event)
            if loop.time() >= deadline:
                raise NativeObservationGate("original_deadline_exhausted") from None
            if operation not in OPERATIONS:
                raise NativeObservationGate("unrecognized_discovery_timeout") from None
            if stop_on_change:
                discarded_binding = before.get("binding")
    raise NativeObservationGate("bounded_read_attempts_exhausted")
