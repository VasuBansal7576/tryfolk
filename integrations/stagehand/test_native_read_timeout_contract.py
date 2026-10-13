"""Actual Playwright exception class at the read-only adapter boundary."""
import asyncio
import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from playwright.async_api import Error, TimeoutError as PlaywrightTimeoutError
from stagehand.rpc_client import RPCError
import adapter
from tryfolk.native_read_observation import NATIVE_CHECKS, NativeObservationGate, read_current_native_controls


class NativeReadTimeoutContract(unittest.IsolatedAsyncioTestCase):
    async def test_actual_missing_iframe_rpc_discards_entire_read_and_independently_rechecks(self):
        calls, checks, events = [], [], []
        selector = "xpath=/html[1]/body[1]/iframe[1]/html[1]/body[1]/input[1]"
        error = RPCError(SimpleNamespace(code=-32603, data=None,
            message="Could not find an element for the given xPath(s): xpath=/html[1]/body[1]/iframe[1]"))

        async def current():
            checks.append(True)
            return {"binding": ("same-native-generation", 1), "checks": {name: True for name in NATIVE_CHECKS}}

        async def actual_read_boundary(page, selector, network, guard, trace):
            calls.append(True)

            async def visibility():
                if len(calls) == 1:
                    raise error
                return {"present": True}

            return await trace.read("stagehand_visibility", visibility)

        async def discover():
            return [await adapter.control_state(None, selector, object(), object(), diagnostic=events.append)]

        with patch.object(adapter, "_control_state_read", actual_read_boundary):
            result = await read_current_native_controls(discover, current,
                deadline=asyncio.get_running_loop().time()+1, on_event=events.append, stop_on_change=True)
        self.assertEqual(result, [{"present": True}])
        self.assertEqual((len(calls), len(checks)), (2, 3))
        self.assertEqual([e["decision"] for e in events if "decision" in e], ["discard_read_missing"])
        self.assertNotIn("xpath=", json.dumps(events))

    async def test_missing_rpc_other_code_operation_or_parent_is_not_recovered(self):
        selector = "xpath=/html[1]/body[1]/iframe[1]/html[1]/body[1]/input[1]"
        for code, operation, message, current_selector, guarded in [
            (-1, "stagehand_visibility", "same", selector, True),
            (-32603, "stagehand_input_value", "same", selector, True),
            (-32603, "stagehand_visibility", "unknown SDK failure", selector, True),
            (-32603, "stagehand_visibility", "same", "#ordinary-owned-control", True),
            (-32603, "stagehand_visibility", "same", selector, False),
        ]:
            if message == "same":
                message = "Could not find an element for the given xPath(s): xpath=/html[1]/body[1]/iframe[1]"
            error = RPCError(SimpleNamespace(code=code, data=None, message=message))

            async def actual_read_boundary(page, selector, network, guard, trace):
                async def missing():
                    raise error
                return await trace.read(operation, missing)

            with patch.object(adapter, "_control_state_read", actual_read_boundary):
                with self.assertRaises(RPCError):
                    await adapter.control_state(None, current_selector,
                        object() if guarded else None, object() if guarded else None)

    async def test_exact_missing_rpc_still_exhausts_at_two_read_only_attempts(self):
        calls = []
        selector = "xpath=/html[1]/body[1]/iframe[1]/input[1]"

        async def current():
            return {"binding": ("same", 1), "checks": {name: True for name in NATIVE_CHECKS}}

        async def boundary(page, selector, network, guard, trace):
            async def missing():
                calls.append(True)
                raise RPCError(SimpleNamespace(code=-32603, data=None,
                    message="Could not find an element for the given xPath(s): xpath=/html[1]/body[1]/iframe[1]"))
            return await trace.read("stagehand_visibility", missing)

        async def discover():
            return [await adapter.control_state(None, selector, object(), object())]

        with patch.object(adapter, "_control_state_read", boundary):
            with self.assertRaisesRegex(NativeObservationGate, "bounded_read_attempts_exhausted"):
                await read_current_native_controls(discover, current,
                    deadline=asyncio.get_running_loop().time()+1, on_event=lambda e: None)
        self.assertEqual(len(calls), 2)

    async def test_stale_iframe_locator_discards_candidates_and_rechecks_before_fresh_read(self):
        calls, checks, events = [], [], []

        async def current():
            checks.append(True)
            return {"binding": ("same-native-generation", 1), "checks": {
                name: True for name in NATIVE_CHECKS}}

        async def actual_read_boundary(page, selector, network, guard, trace):
            calls.append(True)

            async def locator():
                if len(calls) == 1:
                    raise PlaywrightTimeoutError("private-selector-locator timed out")
                return {"present": True}

            return await trace.read("parent_frame_element", locator)

        async def discover():
            return [await adapter.control_state(None, "private-selector", diagnostic=events.append)]

        with patch.object(adapter, "_control_state_read", actual_read_boundary):
            result = await read_current_native_controls(discover, current,
                deadline=asyncio.get_running_loop().time() + 1, on_event=events.append)
        self.assertEqual(result, [{"present": True}])
        self.assertEqual((len(calls), len(checks)), (2, 3))
        discarded = [e for e in events if e.get("decision") == "discard_read_timeout"]
        self.assertEqual(len(discarded), 1)
        self.assertEqual(discarded[0]["operation"], "parent_frame_element")
        self.assertNotIn("private-selector", json.dumps(events))

    async def test_repeated_actual_locator_timeout_remains_bounded_to_two_reads(self):
        calls = []

        async def current():
            return {"binding": ("same-native-generation", 1), "checks": {
                name: True for name in NATIVE_CHECKS}}

        async def actual_read_boundary(page, selector, network, guard, trace):
            async def locator():
                calls.append(True)
                raise PlaywrightTimeoutError("unavailable-current-iframe")
            return await trace.read("parent_frame_element", locator)

        async def discover():
            return [await adapter.control_state(None, "private-selector")]

        with patch.object(adapter, "_control_state_read", actual_read_boundary):
            with self.assertRaisesRegex(NativeObservationGate, "bounded_read_attempts_exhausted"):
                await read_current_native_controls(discover, current,
                    deadline=asyncio.get_running_loop().time() + 1, on_event=lambda e: None)
        self.assertEqual(len(calls), 2)

    async def test_other_sdk_error_and_cancellation_are_not_normalized(self):
        for error in (Error("unknown SDK failure"), asyncio.CancelledError()):
            async def other(*args):
                raise error
            with patch.object(adapter, "_control_state_read", other):
                with self.assertRaises(type(error)):
                    await adapter.control_state(None, "private-selector")


if __name__ == "__main__":
    unittest.main()
