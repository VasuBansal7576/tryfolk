"""Invented offline callbacks only; never real inference, checkout or scale evidence."""

import asyncio
from copy import deepcopy

import pytest

from tryfolk.bulk_policy import prepare_batch, infer_batch, bounded_journeys


def request(index, **changes):
    return {
        "run_id": "fixture-run",
        "persona_id": f"fixture-person-{index}",
        "operation": "decide",
        "context": {"step": 1, "observation_sha256": str(index) * 64, "fixture": True},
        "messages": [{"role": "user", "content": f"Invented separate customer {index}"}],
        "schema": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
        **changes,
    }


def validate(value):
    if not isinstance(value, dict) or set(value) != {"target"} or not isinstance(value["target"], str):
        raise ValueError("Invalid invented typed decision")
    return value


def test_batch_retains_each_original_prompt_and_exact_context():
    requests = [request(1), request(2)]
    batch = prepare_batch(requests)
    assert set(batch["schema"]["properties"]) == {r["persona_id"] for r in requests}
    assert batch["schema"]["required"] == [r["persona_id"] for r in requests]
    assert batch["messages"][1]["content"]["requests"][0]["messages"] == requests[0]["messages"]
    requests[0]["context"]["step"] = 99
    assert batch["bindings"]["fixture-person-1"]["context"]["step"] == 1


@pytest.mark.parametrize(
    "requests",
    [
        [],
        [request(i) for i in range(9)],
        [request(1), request(1)],
        [request(1), request(2, run_id="other-fixture-run")],
        [request(1), request(2, operation="generate")],
        [request(1), request(2, schema={"type": "string"})],
    ],
)
def test_invalid_or_cross_bound_batch_is_rejected(requests):
    with pytest.raises(ValueError):
        prepare_batch(requests)


@pytest.mark.asyncio
async def test_one_invented_batch_callback_returns_two_separate_validated_responses():
    requests = [request(1), request(2)]
    calls = []

    async def inference(messages, schema):
        calls.append((messages, schema))
        return {"fixture-person-1": {"target": "fresh1"}, "fixture-person-2": {"target": "fresh2"}}

    current = {r["persona_id"]: r["context"] for r in requests}
    result = await infer_batch(prepare_batch(requests), inference, validate, current.get)
    assert len(calls) == 1
    assert result == {"fixture-person-1": {"target": "fresh1"}, "fixture-person-2": {"target": "fresh2"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "foreign", "stale", "invalid"])
async def test_batch_output_cannot_cross_persona_or_stale_observation(mutation):
    requests = [request(1), request(2)]
    output = {r["persona_id"]: {"target": "fresh"} for r in requests}
    current = {r["persona_id"]: deepcopy(r["context"]) for r in requests}
    if mutation == "missing":
        output.pop("fixture-person-1")
    if mutation == "foreign":
        output["foreign-person"] = {"target": "fresh"}
    if mutation == "stale":
        current["fixture-person-1"]["step"] = 2
    if mutation == "invalid":
        output["fixture-person-1"] = {"outcome": "fake-success"}

    async def inference(messages, schema):
        return output

    with pytest.raises(ValueError):
        await infer_batch(prepare_batch(requests), inference, validate, current.get)


@pytest.mark.asyncio
async def test_parallel_callback_execution_requires_reviewed_guard_boundary():
    with pytest.raises(ValueError, match="reviewed"):
        await bounded_journeys([request(1)], None, None, concurrency=2)


@pytest.mark.asyncio
async def test_modified_original_batch_cannot_reach_inference():
    batch = prepare_batch([request(1)])
    batch["messages"][1]["content"]["requests"][0]["messages"][0]["content"] = "Changed fixture prompt"
    called = False

    async def inference(messages, schema):
        nonlocal called
        called = True
        return {}

    with pytest.raises(ValueError, match="changed"):
        await infer_batch(batch, inference, validate, lambda _: request(1)["context"])
    assert not called


@pytest.mark.asyncio
async def test_bounded_callbacks_persist_separately_without_duplicate_personas():
    active = maximum = 0
    persisted = []

    async def execute(persona):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        await asyncio.sleep(0)
        active -= 1
        return {"fixture": True, "persona_id": persona["persona_id"]}

    async def persist(persona, result):
        assert persona["persona_id"] == result["persona_id"]
        persisted.append(persona["persona_id"])

    jobs = [request(i) for i in range(20)]
    await bounded_journeys(jobs, execute, persist, concurrency=2, reviewed_guard_boundary=True)
    assert maximum == 2
    assert set(persisted) == {job["persona_id"] for job in jobs}
    assert len(persisted) == 20


@pytest.mark.asyncio
async def test_failure_cancels_and_awaits_inflight_cleanup_without_fake_results():
    cleaned = []
    persisted = []
    both_started = asyncio.Event()
    started = []

    async def execute(persona):
        started.append(persona["persona_id"])
        if len(started) == 2:
            both_started.set()
        try:
            await both_started.wait()
            if persona["persona_id"] == "fixture-person-0":
                raise RuntimeError("Invented tooling failure")
            await asyncio.Event().wait()
        finally:
            cleaned.append(persona["persona_id"])

    async def persist(persona, result):
        persisted.append(result)

    with pytest.raises(RuntimeError, match="Invented tooling failure"):
        await bounded_journeys(
            [request(i) for i in range(10)], execute, persist, concurrency=2, reviewed_guard_boundary=True
        )
    assert len(started) == len(cleaned) == 2
    assert persisted == []
