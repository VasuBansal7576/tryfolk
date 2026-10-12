"""Offline synthetic fixtures only; no model, browser, provider or actual population run."""

from dataclasses import replace
import json
import os

from fastapi.testclient import TestClient
import pytest

from tryfolk.api import create_app
from tryfolk.config import Settings, Target
from tryfolk.service import Service


URL = "http://127.0.0.1:3100"


def environment_settings(monkeypatch, tmp_path, capacity=None):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("TRYFOLK_"):
            monkeypatch.delenv(key)
    if capacity is not None:
        monkeypatch.setenv("TRYFOLK_MAX_PERSONAS", capacity)
    return Settings.from_env()


def fixture_app(monkeypatch, tmp_path, capacity=None):
    settings = environment_settings(monkeypatch, tmp_path, capacity)
    return create_app(
        replace(
            settings,
            db_path=tmp_path / "synthetic.sqlite",
            gates=(),
            targets=(Target(URL, "fixture-revision"),),
        )
    )


def request_quote(client, count):
    response = client.post("/quotes", json={"app_url": URL, "persona_count": count})
    assert response.status_code == 200, response.text
    return response.json()


def assert_no_execution_state(service, quote_id):
    with service.store.transaction() as db:
        for table in ("runs", "jobs", "populations", "reservations", "idempotency", "events"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("SELECT consumed FROM quotes WHERE id=?", (quote_id,)).fetchone()[0] == 0


def test_default_capacity_and_budget_are_unchanged(monkeypatch, tmp_path):
    settings = environment_settings(monkeypatch, tmp_path)
    assert settings.max_personas == 20
    assert settings.budget_microusd == 1_000_000


@pytest.mark.parametrize("capacity", ["1", "20", "50", "100", "1000"])
def test_operator_capacity_is_visible_in_readiness_and_quote(monkeypatch, tmp_path, capacity):
    app = fixture_app(monkeypatch, tmp_path, capacity)
    client = TestClient(app)
    assert client.get("/readiness").json()["max_personas"] == int(capacity)
    quote = request_quote(client, 1)
    assert quote["max_personas"] == int(capacity)
    assert quote["persona_count"] == 1
    assert app.state.service.settings.budget_microusd == 1_000_000


@pytest.mark.parametrize("capacity", ["", " ", "0", "-1", "1001", "50.0", "1e2", "True", "+50", "١"])
def test_invalid_operator_capacity_fails_before_database_creation(monkeypatch, tmp_path, capacity):
    with pytest.raises(ValueError, match="TRYFOLK_MAX_PERSONAS.*1.*1000"):
        environment_settings(monkeypatch, tmp_path, capacity)
    assert not (tmp_path / "runtime/tryfolk.sqlite").exists()


@pytest.mark.parametrize("capacity", [0, -1, 1001, True, False, 50.0, "50"])
def test_direct_settings_cannot_bypass_capacity_validation(capacity):
    with pytest.raises(ValueError, match="TRYFOLK_MAX_PERSONAS.*1.*1000"):
        Settings(max_personas=capacity)


@pytest.mark.parametrize("count", [50, 100, 1000])
def test_above_capacity_quote_retains_n_and_acceptance_creates_nothing(monkeypatch, tmp_path, count):
    app = fixture_app(monkeypatch, tmp_path)
    client = TestClient(app)
    quote = request_quote(client, count)
    assert quote["max_personas"] == 20
    assert quote["persona_count"] == count
    assert quote["ready"] is False
    assert any(gate["code"] == "capacity" for gate in quote["gates"])
    response = client.post(
        "/runs", json={"quote_id": quote["id"]}, headers={"Idempotency-Key": "synthetic-over-capacity"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "capacity"
    assert_no_execution_state(app.state.service, quote["id"])


@pytest.mark.parametrize("count", [50, 100, 1000])
def test_capacity_increase_preserves_both_pass_budget_gate(monkeypatch, tmp_path, count):
    app = fixture_app(monkeypatch, tmp_path, "1000")
    client = TestClient(app)
    quote = request_quote(client, count)
    assert quote["max_personas"] == 1000
    assert quote["persona_count"] == count
    assert quote["ready"] is True
    assert quote["affordable"] is False
    assert quote["before_microusd"] == quote["after_microusd"] == count * 20_000
    assert quote["reserved_microusd"] == count * 40_000 + 50_000
    response = client.post(
        "/runs", json={"quote_id": quote["id"]}, headers={"Idempotency-Key": "synthetic-budget-gate"}
    )
    assert response.status_code == 402
    assert_no_execution_state(app.state.service, quote["id"])


def test_capacity_reduction_after_quote_is_rechecked_before_reservation(monkeypatch, tmp_path):
    app = fixture_app(monkeypatch, tmp_path, "50")
    client = TestClient(app)
    quote = request_quote(client, 21)
    assert quote["ready"] is True and quote["affordable"] is True
    app.state.service.settings = replace(app.state.service.settings, max_personas=20)
    response = client.post(
        "/runs", json={"quote_id": quote["id"]}, headers={"Idempotency-Key": "synthetic-reduced-capacity"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "capacity"
    assert_no_execution_state(app.state.service, quote["id"])


def test_capacity_increase_requires_fresh_quote_and_never_clamps_n(monkeypatch, tmp_path):
    app = fixture_app(monkeypatch, tmp_path)
    client = TestClient(app)
    old_quote = request_quote(client, 21)
    app.state.service.settings = replace(app.state.service.settings, max_personas=50)
    response = client.post(
        "/runs", json={"quote_id": old_quote["id"]}, headers={"Idempotency-Key": "synthetic-stale-capacity"}
    )
    assert response.status_code == 422
    assert_no_execution_state(app.state.service, old_quote["id"])
    fresh_quote = request_quote(client, 21)
    response = client.post(
        "/runs", json={"quote_id": fresh_quote["id"]}, headers={"Idempotency-Key": "synthetic-fresh-capacity"}
    )
    assert response.status_code == 201
    assert response.json()["status"]["persona_count"] == 21
    assert response.json()["status"]["counts"]["generated"] == 0


@pytest.mark.parametrize("count", [0, 1001, 50.5, True, "50"])
def test_request_parser_keeps_strict_public_count_boundary(monkeypatch, tmp_path, count):
    app = fixture_app(monkeypatch, tmp_path, "1000")
    response = TestClient(app).post("/quotes", json={"app_url": URL, "persona_count": count})
    assert response.status_code == 422
    with app.state.service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM quotes").fetchone()[0] == 0


@pytest.mark.parametrize("count", [50, 100, 1000])
def test_synthetic_profiles_and_browser_events_do_not_create_outcomes(tmp_path, count):
    # Deliberately seeded offline view fixture, not a run acceptance or provider receipt.
    service = Service(Settings(db_path=tmp_path / "synthetic-view.sqlite", max_personas=1000))
    persons = [{"id": f"fixture-p{i}", "index": i, "fixture": True} for i in range(count)]
    run = {
        "id": "fixture-run",
        "population_id": "fixture-population",
        "reservation_id": "fixture-run",
        "persona_count": count,
        "phase": "running",
        "outcomes": {},
        "fixture": True,
    }
    with service.store.transaction() as db:
        db.execute("INSERT INTO populations VALUES (?,?)", (run["population_id"], json.dumps(persons)))
        db.execute("INSERT INTO reservations(id,reserved) VALUES (?,?)", (run["id"], 1))
        for person in persons:
            for kind in ("persona_initialized", "browser_action"):
                db.execute(
                    "INSERT INTO events(run_id,persona_id,kind,created_at,data) VALUES (?,?,?,?,?)",
                    (
                        run["id"],
                        person["id"],
                        kind,
                        "fixture-time",
                        json.dumps({"fixture": True, "measured": False}),
                    ),
                )
        db.execute(
            "INSERT INTO events(run_id,persona_id,kind,created_at,data) VALUES (?,?,?,?,?)",
            ("other-fixture-run", persons[0]["id"], "verification", "fixture-time", '{"fixture":true}'),
        )
        initial = service.view(db, run)
        assert len(initial["events"]) == count * 2
        assert initial["counts"]["requested"] == initial["counts"]["generated"] == count
        assert (
            initial["counts"]["executed"]
            == initial["counts"]["completed"]
            == initial["counts"]["failed"]
            == 0
        )
        run["outcomes"] = {
            persons[0]["id"]: {"outcome": "pending", "fixture": True, "measured": False},
            persons[1]["id"]: {"outcome": "failed", "fixture": True, "measured": False},
        }
        recorded = service.view(db, run)
        assert recorded["counts"]["requested"] == count
        assert recorded["counts"]["executed"] == 2
        assert recorded["counts"]["pending"] == recorded["counts"]["failed"] == 1
        assert recorded["counts"]["completed"] == 0
        assert recorded["counts"]["queued"] == count - 2
