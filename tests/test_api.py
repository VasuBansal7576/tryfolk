from pathlib import Path

from fastapi.testclient import TestClient

from tryfolk.api import create_app
from tryfolk.config import Settings, Target


def client_at(path: Path, budget=1_000_000):
    settings = Settings(
        db_path=path,
        budget_microusd=budget,
        owner_token="test-owner",
        targets=(Target(url="http://127.0.0.1:3100", revision="broken-sha", fault="paid-no-access"),),
        gates=(),
    )
    return TestClient(create_app(settings))


def quote(client, count=2):
    response = client.post("/quotes", json={"app_url": "http://127.0.0.1:3100", "persona_count": count})
    assert response.status_code == 200, response.text
    return response.json()


def start(client, q, key="first"):
    return client.post("/runs", json={"quote_id": q["id"]}, headers={"Idempotency-Key": key})


def test_quote_reserves_both_passes_atomically_and_survives_restart(tmp_path):
    db = tmp_path / "runs.sqlite"
    client = client_at(db, budget=150_000)
    q = quote(client)
    assert q["before_microusd"] == 40_000
    assert q["after_microusd"] == 40_000
    assert q["reserved_microusd"] == 130_000  # 50k overhead + 2 x 2 x 20k
    first = start(client, q)
    assert first.status_code == 201, first.text
    q2 = quote(client)
    assert q2["affordable"] is False
    assert start(client, q2, "second").status_code == 402
    restarted = client_at(db, budget=150_000)
    response = restarted.get(
        f"/runs/{first.json()['id']}", headers={"Authorization": "Bearer " + first.json()["access_token"]}
    )
    assert response.json()["budget"]["reserved_microusd"] == 130_000
    assert quote(restarted)["available_microusd"] == 20_000


def auth(run):
    return {"Authorization": "Bearer " + run["access_token"]}


def test_idempotency_never_reserves_twice_and_conflicting_key_is_rejected(tmp_path):
    client = client_at(tmp_path / "runs.sqlite")
    q = quote(client)
    first = start(client, q).json()
    assert start(client, q).json() == first
    assert quote(client)["available_microusd"] == 870_000
    assert start(client, quote(client), "first").status_code == 409
    assert client.get(f"/runs/{first['id']}").status_code == 401


def test_cancel_is_durable_idempotent_and_releases_unused_both_pass_budget(tmp_path):
    db = tmp_path / "runs.sqlite"
    client = client_at(db)
    run = start(client, quote(client)).json()
    response = client.post(f"/runs/{run['id']}/cancel", headers=auth(run))
    assert response.status_code == 200
    assert response.json()["phase"] == "cancelled"
    assert response.json()["budget"]["released_microusd"] == 130_000
    assert (
        client.post(f"/runs/{run['id']}/cancel", headers=auth(run)).json()["budget"]
        == response.json()["budget"]
    )
    assert quote(client_at(db))["available_microusd"] == 1_000_000


def test_unsafe_or_unconnected_origins_are_rejected_and_count_is_not_shrunk(tmp_path):
    client = client_at(tmp_path / "runs.sqlite")
    for url in [
        "http://169.254.169.254",
        "file:///etc/passwd",
        "http://localhost:3100",
        "http://x@127.0.0.1:3100",
        "http://127.0.0.1:3100/other",
    ]:
        assert client.post("/quotes", json={"app_url": url, "persona_count": 1}).status_code == 422
    q = quote(client, 21)
    assert q["persona_count"] == 21
    assert q["ready"] is False
    assert q["gates"][0]["code"] == "capacity"
    run = start(client, q).json()
    assert run["status"]["counts"]["blocked"] == 21
    assert run["status"]["counts"]["executed"] == 0


def test_owner_session_is_http_only_scoped_and_csrf_protected(tmp_path):
    client = client_at(tmp_path / "runs.sqlite")
    run = start(client, quote(client)).json()
    assert (
        client.post(
            "/owner/session", json={"secret": "test-owner"}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/owner/session", json={"secret": "wrong"}, headers={"Origin": "http://127.0.0.1:8000"}
        ).status_code
        == 401
    )
    login = client.post(
        "/owner/session", json={"secret": "test-owner"}, headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert client.get("/owner/session").json()["owner"] is True
    assert client.get(f"/runs/{run['id']}").status_code == 200
    assert client.post(f"/runs/{run['id']}/cancel").status_code == 403
    csrf = login.json()["csrf_token"]
    assert (
        client.post(
            f"/runs/{run['id']}/cancel", headers={"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    assert (
        client.delete(
            "/owner/session", headers={"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": csrf}
        ).status_code
        == 200
    )
    assert client.get("/owner/session").json()["owner"] is False


def test_two_concurrent_runs_cannot_overbook_same_budget(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    client = client_at(tmp_path / "runs.sqlite", budget=150_000)
    first, second = quote(client), quote(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda item: start(client, item[0], item[1]), [(first, "a"), (second, "b")])
        )
    assert sorted(response.status_code for response in responses) == [201, 402]
    assert quote(client)["available_microusd"] == 20_000


def test_connected_app_prefers_registered_owned_broken_dodo_over_historical_fixture(tmp_path):
    client = TestClient(
        create_app(
            Settings(
                db_path=tmp_path / "state.sqlite",
                gates=(),
                targets=(
                    Target(url="http://127.0.0.1:3101", revision="fixture", fault="paid-no-access"),
                    Target(url="http://127.0.0.1:3300", revision="healthy", provider="dodo", mode="healthy"),
                    Target(
                        url="http://127.0.0.1:3301", revision="controlled", provider="dodo", mode="broken"
                    ),
                ),
            )
        )
    )
    assert client.get("/readiness").json()["target_url"] == "http://127.0.0.1:3301"


def test_cfo_export_forecast_cannot_underreserve_after_pass_or_raise_owner_cap(tmp_path):
    import json
    from dataclasses import replace

    plan = tmp_path / "cfo-plan.json"
    plan.write_text(
        json.dumps(
            {
                "source": "cfo.ai-inspected-plan",
                "source_url": "https://app.cfo.ai/fixture-plan",
                "plan_id": "UNIT-TEST-FIXTURE",
                "snapshot_sha256": "a" * 64,
                "assumptions": {
                    "before_per_persona_microusd": 30_000,
                    "after_per_persona_microusd": 50_000,
                    "discovery_microusd": 10_000,
                    "generation_microusd": 10_000,
                    "investigation_microusd": 10_000,
                    "repair_microusd": 20_000,
                    "known_replay_microusd": 10_000,
                    "event_budget_microusd": 180_000,
                },
            }
        )
    )
    base = client_at(tmp_path / "runs.sqlite").app.state.service.settings
    client = TestClient(create_app(replace(base, cfo_plan_path=plan, budget_microusd=200_000)))
    q = quote(client, 2)
    assert q["before_microusd"] == 60_000
    assert q["after_microusd"] == 100_000
    assert q["overhead_microusd"] == 60_000
    assert q["available_microusd"] == 180_000
    assert q["affordable"] is False
    assert q["cfo_plan_status"] == "imported"
    assert start(client, q).status_code == 402


def test_quote_revision_is_rechecked_before_reservation(tmp_path):
    from dataclasses import replace

    original = client_at(tmp_path / "runs.sqlite")
    q = quote(original)
    changed = replace(
        original.app.state.service.settings, targets=(Target(url=q["app_url"], revision="changed"),)
    )
    current = TestClient(create_app(changed))
    assert start(current, q).status_code == 409
    assert quote(current)["available_microusd"] == 1_000_000


def test_recorded_screenshot_is_run_authorized_and_cannot_read_other_files(tmp_path, monkeypatch):
    import base64
    import json

    root = tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(root))
    client = client_at(tmp_path / "runs.sqlite")
    run = start(client, quote(client, 1)).json()
    run_id = run["id"]
    image = root / (run_id + "-p-0.png")
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP0UAAAAASUVORK5CYII="
        )
    )
    service = client.app.state.service
    with service.store.transaction() as db:
        db.execute(
            "INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
            (run_id, "observation", "now", json.dumps({"screenshot": str(image)})),
        )
    url = f"/runs/{run_id}/evidence/{image.name}"
    assert client.get(url).status_code == 401
    status = client.get("/runs/" + run_id, headers=auth(run))
    assert status.status_code == 200
    assert status.json()["events"][0]["data"]["screenshot"] == url
    assert "HttpOnly" in status.headers["set-cookie"]
    assert client.get(url).content == image.read_bytes()
    another = start(client, quote(client, 1), "other").json()
    assert (
        client.get(f"/runs/{another['id']}/evidence/{image.name}", headers=auth(another)).status_code == 404
    )
    assert client.get(f"/runs/{run_id}/evidence/local-secrets.json").status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_cfo_missing_plan_gate_preserves_both_pass_quote_without_claiming_account_inactive(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    client = TestClient(
        create_app(
            Settings(
                db_path=tmp_path / "runs.sqlite",
                owner_token="test-owner",
                gates=(),
                targets=(
                    Target(
                        url="http://127.0.0.1:3301",
                        revision="owned-revision",
                        provider="dodo",
                        repo_path=str(tmp_path),
                    ),
                ),
            )
        )
    )
    readiness = client.get("/readiness").json()
    assert "inactive" not in readiness["integrations"]["cfo"]["detail"].lower()
    result = client.post("/quotes", json={"app_url": "http://127.0.0.1:3301", "persona_count": 1}).json()
    assert result["cfo_plan_status"] == "missing"
    assert result["provenance"]["cost_basis"] == "non_cfo_estimate" and result["ready"] is False
    assert result["before_microusd"] == result["after_microusd"] == 20_000
    assert result["overhead_microusd"] == 50_000 and result["reserved_microusd"] == 90_000
    gate = next(gate for gate in result["gates"] if gate["code"] == "cfo_plan_missing")
    assert "inactive" not in gate["message"].lower() and "import" in gate["action"].lower()
