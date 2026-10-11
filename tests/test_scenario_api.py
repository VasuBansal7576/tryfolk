"""Public-interface scenario/seed tests; all journeys below are disclosed I/O fixtures."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tryfolk.api import create_app
from tryfolk.config import Settings, Target
from tryfolk.worker import Worker
from test_api import auth, start
from test_worker import FixtureIO

URL = "http://127.0.0.1:3100"
CONDITIONS = {"account_history": "new", "device": "desktop", "locale": "en-US"}


def scenario(identifier, **overrides):
    return {
        "id": identifier,
        "area": "one_time_payment",
        "title": identifier,
        "customer_context": CONDITIONS | {"background": "An adult buying artwork"},
        "hypothetical_problem": "Purchase succeeds but access may be delayed.",
        "customer_goal": "Buy the artwork and download it.",
        "expected_behavior": ["Correlate independent payment and matching entitlement."],
        "check_kind": "customer_journey",
        "availability": "runnable",
        "exercise_status": "not_exercised",
        "risk_priority": 10,
        "required_capabilities": {"target": ["one_time_checkout"], "provider": [], "conditions": CONDITIONS},
        "provider_support": {"status": "documented", "source_refs": []},
        "source_refs": ["owned source fixture"],
        "evidence_requirements": ["payment", "entitlement"],
        "verification_boundary": {"limits": "Catalog inputs are never measured evidence."},
        "expected_outcome": "fulfilled",
        **overrides,
    }


def configured(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": "tryfolk.payment-scenarios.v1",
                "version": "fixture-v1",
                "scenarios": [
                    scenario("checkout", risk_priority=20),
                    scenario("access", risk_priority=30),
                    scenario("decline", availability="requires_fixture"),
                    scenario("tax", check_kind="provider_control", availability="provider_owned"),
                    scenario(
                        "renewal",
                        required_capabilities={"target": ["subscription"], "provider": [], "conditions": {}},
                    ),
                ],
            }
        )
    )
    bank = tmp_path / "seeds.json"
    bank.write_text(
        json.dumps(
            {
                "schema_version": "tryfolk.persona-seeds.v1",
                "version": "fixture-seeds-v1",
                "source": {
                    "dataset": "Disclosed test fixture",
                    "url": "https://example.org/fixture",
                    "revision": "fixture-rev",
                    "license": "CC-BY-4.0",
                    "attribution": "Fixture authors",
                },
                "seeds": [
                    {
                        "id": f"seed-{i}",
                        "profile": {"background": f"Adult illustrator customer {i}"},
                        "relevance_tags": ["visual_art"],
                        "source_record_id": str(i),
                        "supported_context": {k: [v] for k, v in CONDITIONS.items()},
                        "eligibility": {"adult": True, "commercial_use": True, "quality_passed": True},
                    }
                    for i in range(4)
                ],
            }
        )
    )
    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        owner_token="test-owner",
        gates=(),
        scenario_catalog_path=catalog,
        seed_bank_path=bank,
        seed_bank_min_records=4,
        targets=(
            Target(
                url=URL,
                revision="broken-sha",
                fault="paid-no-access",
                repo_path=str(tmp_path),
                scenario_capabilities={"target": ["one_time_checkout"], "provider": []},
                scenario_conditions=CONDITIONS,
            ),
        ),
    )
    return settings, catalog, bank


def get_quote(client, count=2, **extra):
    return client.post("/quotes", json={"app_url": URL, "persona_count": count, **extra})


def test_selection_is_stable_supported_risk_first_and_not_catalog_coverage(tmp_path):
    settings, _, _ = configured(tmp_path)
    client = TestClient(create_app(settings))
    catalog = client.get("/scenarios", params={"app_url": URL})
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["catalog_case_count"] == 5
    q = get_quote(client).json()
    plan = q["scenario_plan"]
    assert [a["scenario_id"] for a in plan["assignments"]] == ["access", "checkout"]
    assert get_quote(client).json()["scenario_plan"] == plan
    assert plan["selection_rationale"]["omitted_cases"] == [
        {"scenario_id": "decline", "reason": "requires_fixture"},
        {"scenario_id": "tax", "reason": "provider_owned"},
        {"scenario_id": "renewal", "reason": "missing_capabilities_or_conditions"},
    ]
    assert plan["selection_rationale"]["library_seed_count"] == 4
    assert plan["does_not_imply_exercised_coverage"] is True
    assert plan["requested_personas"] == q["persona_count"] == 2
    assert q["reserved_microusd"] == 130_000
    assert client.get("/scenarios", params={"app_url": "http://169.254.169.254"}).status_code == 422


@pytest.mark.parametrize(
    "ids",
    [["access"], ["unknown", "access"], ["tax", "access"], ["decline", "access"], ["renewal", "access"]],
)
def test_manual_selection_rejects_wrong_n_or_unexecutable_cases_without_reservation(tmp_path, ids):
    settings, _, _ = configured(tmp_path)
    client = TestClient(create_app(settings))
    result = get_quote(client, scenario_ids=ids)
    assert result.status_code == 422, result.text
    assert get_quote(client).json()["available_microusd"] == 1_000_000


def test_repeated_case_uses_distinct_seed_records_without_expanding_n(tmp_path):
    settings, _, _ = configured(tmp_path)
    client = TestClient(create_app(settings))
    q = get_quote(client, scenario_ids=["access", "access"]).json()
    assignments = q["scenario_plan"]["assignments"]
    assert len({a["seed"]["id"] for a in assignments}) == 2
    assert q["scenario_plan"]["selection_rationale"]["mode"] == "manual"
    assert q["scenario_plan"]["selection_rationale"]["personality_variation_is_case_coverage"] is False


def test_missing_bank_or_insufficient_eligible_seeds_gates_exact_n(tmp_path):
    settings, _, bank = configured(tmp_path)
    client = TestClient(create_app(replace(settings, seed_bank_path=None)))
    q = get_quote(client).json()
    assert q["ready"] is False
    assert q["gates"][0]["code"] == "seed_bank_missing"
    run = start(client, q).json()
    assert run["status"]["counts"]["blocked"] == 2
    data = json.loads(bank.read_text())
    data["seeds"][0]["eligibility"]["quality_passed"] = False
    bank.write_text(json.dumps(data))
    rejected = get_quote(TestClient(create_app(settings)))
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "scenario_data_invalid"


class ScenarioIO(FixtureIO):
    def __init__(self):
        super().__init__()
        self.briefs = []
        self.personas = []

    async def generate(self, brief, count):
        self.briefs.append(brief)
        persons = await super().generate(brief, count)
        for p in persons:
            p["conditions"] = CONDITIONS
            p["initial_snapshot"] = {"persona": p["profile"], "memory": []}
            p["initial_semantic_memory"] = [{"type": "information", "content": "Buy artwork."}]
        return persons

    async def journey(self, run, persona, cancelled, emit):
        self.personas.append(json.loads(json.dumps(persona)))
        return await super().journey(run, persona, cancelled, emit)


@pytest.mark.asyncio
async def test_quote_freezes_data_generation_saves_actual_memory_and_replay_uses_exact_population(tmp_path):
    settings, catalog, bank = configured(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    q = get_quote(client).json()
    # Accepted quote never reloads the changed catalog or bank, including after restart.
    catalog.write_text("{}")
    bank.write_text("{}")
    app = create_app(settings)
    client = TestClient(app)
    before = start(client, q).json()
    io = ScenarioIO()
    worker = Worker(app.state.service, io)
    await worker.tick()
    b = client.get(f"/runs/{before['id']}", headers=auth(before)).json()
    assert b["phase"] == "completed", b["gates"]
    assert io.briefs[0]["generation_plan"] == q["scenario_plan"]
    assert b["scenario_plan"] == q["scenario_plan"]
    assert b["scenario_coverage"]["exercised_case_ids"] == []  # Fixture outcomes do not prove exercise.
    assert b["scenario_coverage"]["whole_catalog_coverage_claimed"] is False
    for persona in b["personas"]:
        assigned = persona["assigned_scenario"]
        assert assigned["profile_sha256"] and assigned["initial_memory_sha256"]
        assert assigned["seed"]["source"]["license"] == "CC-BY-4.0"
        assert assigned["condition_fixture_verified"] is False
    owner = {"Authorization": "Bearer test-owner"}
    finding = client.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    repair = client.post(
        f"/runs/{before['id']}/repairs", headers=owner, json={"finding_id": finding["id"]}
    ).json()
    app.state.service.register_repair(
        repair["id"],
        head_revision="fixed-sha",
        preview_url="http://127.0.0.1:3102",
        checkpoint_id="fixture-only",
        checkpoint_url=None,
        provider="local-fixture",
    )
    after = client.post(
        f"/runs/{before['id']}/replay",
        headers=owner | {"Idempotency-Key": "after"},
        json={"repair_id": repair["id"]},
    ).json()
    await worker.tick()
    a = client.get(f"/runs/{after['id']}", headers=auth(after)).json()
    assert a["personas"] == b["personas"]
    assert a["scenario_plan"] == b["scenario_plan"]
    assert io.personas[:2] == io.personas[2:]
    assert len(io.briefs) == io.discovery_calls == 1
    assert a["budget"]["reserved_microusd"] == b["budget"]["reserved_microusd"] == 130_000


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["memory", "id", "name", "profile", "conditions", "seed", "snapshot"])
async def test_tampered_saved_profile_blocks_replay_before_any_journey_or_new_reservation(tmp_path, tamper):
    settings, _, _ = configured(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    before = start(client, get_quote(client).json()).json()
    io = ScenarioIO()
    await Worker(app.state.service, io).tick()
    with app.state.service.store.transaction() as db:
        row = db.execute("SELECT id,payload FROM populations").fetchone()
        persons = json.loads(row["payload"])
        if tamper == "memory":
            persons[0]["initial_semantic_memory"][0]["content"] = "Different goal"
        elif tamper == "seed":
            persons[0]["assigned_scenario"]["seed"]["record_snapshot"]["profile"] = {
                "background": "Other seed"
            }
        elif tamper == "snapshot":
            persons[0]["initial_snapshot"]["memory"] = ["Changed memory"]
        else:
            persons[0][tamper] = "Changed identity or conditions"
        db.execute("UPDATE populations SET payload=? WHERE id=?", (json.dumps(persons), row["id"]))
    owner = {"Authorization": "Bearer test-owner"}
    finding = client.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    repair = client.post(
        f"/runs/{before['id']}/repairs", headers=owner, json={"finding_id": finding["id"]}
    ).json()
    app.state.service.register_repair(
        repair["id"],
        head_revision="fixed-sha",
        preview_url="http://127.0.0.1:3102",
        checkpoint_id="fixture-only",
        checkpoint_url=None,
        provider="local-fixture",
    )
    result = client.post(
        f"/runs/{before['id']}/replay",
        headers=owner | {"Idempotency-Key": "after"},
        json={"repair_id": repair["id"]},
    )
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "population_integrity"
    assert len(io.personas) == 2


def test_changed_registered_conditions_reject_quote_before_reservation(tmp_path):
    settings, _, _ = configured(tmp_path)
    q = get_quote(TestClient(create_app(settings))).json()
    changed = replace(
        settings,
        targets=(replace(settings.targets[0], scenario_conditions=CONDITIONS | {"locale": "fr-FR"}),),
    )
    client = TestClient(create_app(changed))
    result = start(client, q)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "scenario_support_changed"
    with client.app.state.service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_generated_imaginary_condition_blocks_all_journeys_and_preserves_after_reserve(tmp_path):
    class WrongCondition(ScenarioIO):
        async def generate(self, brief, count):
            persons = await super().generate(brief, count)
            persons[0]["conditions"] = CONDITIONS | {"network": "slow"}
            return persons

    settings, _, _ = configured(tmp_path)
    app = create_app(settings)
    client = TestClient(app)
    run = start(client, get_quote(client).json()).json()
    io = WrongCondition()
    await Worker(app.state.service, io).tick()
    status = client.get(f"/runs/{run['id']}", headers=auth(run)).json()
    assert status["phase"] == "blocked"
    assert status["personas"] == []
    assert io.personas == []
    assert status["budget"]["reserved_microusd"] == 130_000
    assert status["budget"]["spent_microusd"] == 20_000
    assert status["budget"]["unused_microusd"] == 110_000


def test_two_thousand_record_import_and_selection_is_bounded_and_seed_scope_isolated(tmp_path):
    from tryfolk.scenarios import generation_constraints

    settings, _, bank = configured(tmp_path)
    data = json.loads(bank.read_text())
    prototype = data["seeds"][0]
    data["seeds"] = [
        prototype
        | {
            "id": f"public-fixture-{i}",
            "source_record_id": str(i),
            "profile": {"background": f"Adult illustrator fixture customer {i}"},
        }
        for i in range(2000)
    ]
    bank.write_text(json.dumps(data))
    client = TestClient(create_app(replace(settings, seed_bank_min_records=2000)))
    q = get_quote(client, count=20).json()
    assert q["ready"] is True
    plan = q["scenario_plan"]
    assert plan["selection_rationale"]["library_seed_count"] == 2000
    assert len(plan["assignments"]) == 20
    assert len({a["seed"]["id"] for a in plan["assignments"]}) == 20
    assert plan["selection_rationale"]["selected_case_ids"] == ["access", "checkout"]
    constraint = generation_constraints({"generation_plan": plan}, 0)
    assert constraint["seed_profile"] == plan["assignments"][0]["seed"]["record_snapshot"]["profile"]
    assert "generation_plan" not in constraint
    assert "assignments" not in constraint
    assert constraint["conditions"] == CONDITIONS


def test_quote_rejects_population_override_and_missing_scenario_catalog_is_actionable(tmp_path):
    settings, _, _ = configured(tmp_path)
    client = TestClient(create_app(settings))
    assert get_quote(client, persona_count_after=1).status_code == 422
    missing = TestClient(create_app(replace(settings, scenario_catalog_path=None)))
    assert missing.get("/scenarios", params={"app_url": URL}).status_code == 409
    assert get_quote(missing, scenario_ids=["access", "checkout"]).status_code == 422


def test_genuine_owned_catalog_requires_registered_fixture_and_preserves_all_hypotheses(tmp_path):
    from pathlib import Path

    settings, _, _ = configured(tmp_path)
    path = Path(__file__).resolve().parents[1] / "dodo-target/scenarios/payment-catalog.json"
    original = json.loads(path.read_text())
    runnable = [s for s in original["scenarios"] if s["availability"] == "runnable"]
    target = replace(
        settings.targets[0],
        scenario_capabilities={
            k: list({cap for s in runnable for cap in s["required_capabilities"][k]})
            for k in ("target", "provider")
        },
        scenario_fixture_ids=("dodo-public-success-card-us-desktop-v1",),
    )
    settings = replace(settings, scenario_catalog_path=path, targets=(target,))
    client = TestClient(create_app(settings))
    response = client.get("/scenarios", params={"app_url": URL})
    assert response.status_code == 200, response.text
    assert response.json()["catalog_case_count"] == 24
    assert response.json()["catalog_version"] == original["catalog_version"]
    assert sum(s["selection_ready"] for s in response.json()["scenarios"]) == 2
    assert all(s["exercise_status"] == "not_exercised" for s in response.json()["scenarios"])
    q = get_quote(client).json()
    assert set(q["scenario_plan"]["selection_rationale"]["selected_case_ids"]) == {s["id"] for s in runnable}
    assert len(q["scenario_plan"]["selection_rationale"]["omitted_cases"]) == 22
    assert q["reserved_microusd"] == 130_000
    missing = TestClient(create_app(replace(settings, targets=(replace(target, scenario_fixture_ids=()),))))
    q2 = get_quote(missing).json()
    assert q2["ready"] is False
    assert q2["scenario_plan"]["assignments"] == []
    assert q2["gates"][0]["code"] == "scenario_support_missing"


def test_context_unrelated_seeds_do_not_become_random_customer_assignments(tmp_path):
    settings, _, bank = configured(tmp_path)
    data = json.loads(bank.read_text())
    for seed in data["seeds"]:
        seed["profile"] = {"background": "Adult with a horticulture background"}
        seed["relevance_tags"] = ["horticulture"]
    bank.write_text(json.dumps(data))
    client = TestClient(create_app(settings))
    response = get_quote(client)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "scenario_data_invalid"
    with client.app.state.service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 0


@pytest.mark.parametrize("corruption", ["duplicate_id", "invalid_risk", "invalid_fixture"])
def test_malformed_local_catalog_returns_setup_error_before_quote_or_budget_mutation(tmp_path, corruption):
    settings, path, _ = configured(tmp_path)
    data = json.loads(path.read_text())
    if corruption == "duplicate_id":
        data["scenarios"][1]["id"] = data["scenarios"][0]["id"]
    elif corruption == "invalid_risk":
        data["scenarios"][0]["risk"] = ["high"]
    else:
        data["scenarios"][0]["condition_fixture"] = {"id": 42, "registration": "existing_driver"}
    path.write_text(json.dumps(data))
    client = TestClient(create_app(settings))
    response = get_quote(client)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "scenario_data_invalid"
    with client.app.state.service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM quotes").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 0
