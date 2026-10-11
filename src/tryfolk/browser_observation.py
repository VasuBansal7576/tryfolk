"""Discard stale inference before exposing any executable browser action."""
import asyncio
from .browser_policy import FIELD_METHODS, SELECT_METHODS
from .browser_settlement import DOCUMENT_STATE


def same_document(before, after):
    return all(before.get(k) == after.get(k) for k in ("url", "generation", "control_signature"))


async def observe_fresh(page, policy, infer, control_state, *, timeout=170, max_attempts=3):
    deadline = asyncio.get_running_loop().time() + timeout
    discarded = []

    async def read(operation):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise RuntimeError("Fresh observation deadline exhausted; no browser action was repeated.")
        try:
            return await asyncio.wait_for(operation(), remaining)
        except TimeoutError as error:
            raise RuntimeError("Fresh observation deadline exhausted; no browser action was repeated.") from error

    async def document():
        state = await read(lambda: page.evaluate(DOCUMENT_STATE))
        policy.check_url(state["url"])
        return state

    for attempt in range(1, max_attempts + 1):
        before = await document()
        if before.get("ready_state") not in ("interactive", "complete") or not before.get("body_present"):
            discarded.append({"attempt":attempt, "reason":"document_not_ready"})
            await read(lambda: asyncio.sleep(0.05))
            continue
        proposed = await read(infer)
        after = await document()
        if not same_document(before, after):
            discarded.append({"attempt":attempt, "reason":"document_changed_during_inference"})
            continue
        actions, fields, unsupported = [], [], []
        stale = None
        for action in proposed:
            control = await read(lambda: control_state(action["selector"]))
            if control.get("present") is False:
                stale = {"attempt":attempt, "reason":control.get("reason", "missing_control"), "selector":action["selector"]}
                break
            try:
                bound = policy.bind(action, control, before["url"])
            except ValueError as error:
                unsupported.append({"description":action.get("description"), "reason":str(error)})
                if control.get("required") and not control.get("value") and not control.get("disabled"):
                    raise RuntimeError("Required checkout field is unsupported by the approved synthetic contract.") from error
                continue
            if bound["_tryfolk"].get("field"):
                approved = False
                try:
                    policy.validate_completion(bound, control, before["url"])
                    approved = True
                except ValueError:
                    pass
                fields.append({"field":bound["_tryfolk"]["field"], "approved_value_present":approved,
                               "metadata_source":control.get("metadata_source", "stagehand_current_dom")})
            if action.get("method") in FIELD_METHODS and (
                control.get("value") == bound["arguments"][0]
                or (action.get("method") not in SELECT_METHODS and control.get("value"))
            ):
                continue
            actions.append(bound)
        text = await read(lambda: page.evaluate("document.body.innerText"))
        final = await document()
        if stale or not same_document(before, final):
            discarded.append(stale or {"attempt":attempt, "reason":"document_changed_during_metadata"})
            continue
        return {"actions":actions, "unsupported_controls":unsupported, "field_evidence":fields,
                "text":text, "url":final["url"], "document":final,
                "freshness":{"status":"fresh", "attempts":attempt, "discarded":discarded}}
    error = RuntimeError("No stable fresh SDK observation after bounded read-only attempts; no browser action was repeated.")
    error.observation_evidence = {"status":"stale", "discarded":discarded}
    raise error
