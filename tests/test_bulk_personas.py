"""Invented SDK/model fixtures only; no actual population or payment evidence."""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from tryfolk.scenarios import digest
from tryfolk.bulk_personas import validated_purchase_plan, choose_planned_action


def adapter():
    path = Path(__file__).parents[1] / "integrations/tinytroupe/bulk_adapter.py"
    spec = importlib.util.spec_from_file_location("bulk_adapter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Memory:
    def __init__(self):
        self.entries = []

    def store(self, entry):
        self.entries.append(deepcopy(entry))

    def retrieve_all(self):
        return deepcopy(self.entries)


class Person:
    def __init__(self, name, mental_faculties):
        self.name = name
        self.profile = {"name": name}
        self.semantic_memory, self.episodic_memory = Memory(), Memory()

    def define(self, key, value):
        self.profile[key] = deepcopy(value)

    def listen(self, text):
        self.episodic_memory.store({"stimulus": text})

    def to_json(self):
        return {"persona": deepcopy(self.profile), "memory": self.episodic_memory.retrieve_all()}


def command(count=2, start=0):
    assignments = []
    for index in range(count + start):
        seed = {
            "id": f"seed-{index}",
            "profile": {"interests": [f"art-{index}"]},
            "adult_verified": True,
            "eligibility": {"adult": True, "commercial_use": True, "quality_passed": True},
            "supported_context": {"billing_country": ["US"], "locale": ["en-US"]},
        }
        scenario = {
            "id": "access",
            "customer_goal": "buy art and use download",
            "customer_context": "new art buyer",
            "hypothetical_problem": "payment may succeed without access",
        }
        conditions = {"billing_country": "US", "locale": "en-US"}
        assignments.append(
            {
                "persona_index": index,
                "scenario_snapshot": scenario,
                "scenario_sha256": digest(scenario),
                "condition_fixture_snapshot": conditions,
                "condition_fixture_sha256": digest(conditions),
                "seed": {
                    "id": seed["id"],
                    "record_snapshot": seed,
                    "record_sha256": digest(seed),
                    "source": {
                        "license": "CC-BY-4.0",
                        "revision": "pinned",
                        "dataset": "invented",
                        "url": "https://example.invalid/seeds",
                        "attribution": "Invented fixture",
                    },
                },
            }
        )
    return {
        "count": count,
        "start_index": start,
        "run_id": "a" * 32,
        "brief": {
            "provider": "dodo",
            "generation_plan": {"requested_personas": count + start, "assignments": assignments},
        },
    }


def inferred(messages, schema, **kwargs):
    return {
        pid: {
            "purchase": index % 2 == 0,
            "rationale": f"Individual rationale {index}",
            "max_steps": 24,
            "unexpected_state": "reconsider",
        }
        for index, pid in enumerate(schema["properties"])
    }


def test_chunk_preserves_distinct_individual_plans_and_real_sdk_memory_seam():
    original = command(start=3)
    population = adapter().generate_bulk(original, Person, inferred)["population"]
    assert [p["index"] for p in population] == [3, 4]
    assert len({p["id"] for p in population}) == 2
    assert [validated_purchase_plan(p)["purchase"] for p in population] == [True, False]
    assert all(p["initial_snapshot"]["memory"] and p["initial_semantic_memory"] for p in population)
    assert original == command(start=3)
    population[0]["profile"]["interests"].append("modified")
    with pytest.raises(ValueError, match="profile"):
        validated_purchase_plan(population[0])


@pytest.mark.parametrize("mutation", ["missing", "foreign", "invalid_type", "tool_payload"])
def test_model_output_cannot_omit_persona_change_type_or_supply_tools(mutation):
    def inference(messages, schema, **kwargs):
        output = inferred(messages, schema)
        first = next(iter(output))
        if mutation == "missing":
            del output[first]
        if mutation == "foreign":
            output["other"] = output.pop(first)
        if mutation == "invalid_type":
            output[first]["purchase"] = "yes"
        if mutation == "tool_payload":
            output[first]["selector"] = "arbitrary"
        return output

    with pytest.raises(ValueError):
        adapter().generate_bulk(command(), Person, inference)


@pytest.mark.parametrize("mutation", ["license", "adult", "country", "scenario", "condition", "duplicate"])
def test_invalid_seed_or_assignment_stops_before_sdk_and_model(mutation):
    cmd = command()
    assignments = cmd["brief"]["generation_plan"]["assignments"]
    a = assignments[0]
    if mutation == "license":
        a["seed"]["source"]["license"] = "unknown"
    if mutation == "adult":
        a["seed"]["record_snapshot"]["eligibility"]["adult"] = False
        a["seed"]["record_sha256"] = digest(a["seed"]["record_snapshot"])
    if mutation == "country":
        a["condition_fixture_snapshot"]["billing_country"] = "CA"
        a["condition_fixture_sha256"] = digest(a["condition_fixture_snapshot"])
    if mutation == "scenario":
        a["scenario_snapshot"]["customer_goal"] = "changed"
    if mutation == "condition":
        a["condition_fixture_sha256"] = "0" * 64
    if mutation == "duplicate":
        assignments[1]["seed"] = deepcopy(a["seed"])

    def forbidden(*args, **kwargs):
        raise AssertionError("SDK or model called before validation")

    with pytest.raises(ValueError):
        adapter().generate_bulk(cmd, forbidden, forbidden)


def test_saved_plan_refuses_scenario_or_identity_tampering():
    person = adapter().generate_bulk(command(), Person, inferred)["population"][0]
    person["id"] = "other"
    with pytest.raises(ValueError, match="identity"):
        validated_purchase_plan(person)
    person = adapter().generate_bulk(command(), Person, inferred)["population"][0]
    person["bulk_scenario_snapshot"]["customer_goal"] = "changed"
    with pytest.raises(ValueError, match="scenario"):
        validated_purchase_plan(person)


def action(kind=None, field=None):
    return {
        "method": "fill" if field else "click",
        "selector": "fresh-only",
        "arguments": [],
        "_tryfolk": {"kind": kind, **({"field": field} if field else {})},
    }


def test_executor_chooses_only_fresh_policy_bound_controls_never_repeats_pay():
    plan = adapter().generate_bulk(command(), Person, inferred)["population"][0]["purchase_plan"]
    country, postal, pay = action(field="country"), action(field="postal"), action("pay")
    assert choose_planned_action(plan, [pay, postal, country], []) is country
    assert choose_planned_action(plan, [pay], [pay]) is None
    assert choose_planned_action(plan, [action("arbitrary-navigation")], []) is None
    assert choose_planned_action({**plan, "purchase": False}, [pay], []) is None
    assert choose_planned_action({**plan, "max_steps": 1}, [pay], [country]) is None
    for field in ("card_name", "card_month", "card_year"):
        fresh = action(field=field)
        assert choose_planned_action(plan, [fresh], []) is fresh


@pytest.mark.parametrize("requested", [100, 500, 1000])
def test_full_population_chunks_retain_exact_ids_indices_and_frozen_replay_binding(requested):
    from tryfolk.scenarios import freeze_population, validate_population

    cmd = command(count=64, start=requested - 64)
    population = []
    for index in range(0, requested, 32):
        chunk = {**cmd, "start_index": index, "count": min(32, requested - index)}
        population += adapter().generate_bulk(chunk, Person, inferred)["population"]
    assert [p["index"] for p in population] == list(range(requested))
    assert len({p["id"] for p in population}) == requested
    plan = cmd["brief"]["generation_plan"]
    population = freeze_population(population, plan)
    validate_population(population, plan)
    assert all(validated_purchase_plan(p) for p in population)
    population[37]["purchase_plan"]["purchase"] = not population[37]["purchase_plan"]["purchase"]
    with pytest.raises(ValueError):
        validate_population(population, plan)
