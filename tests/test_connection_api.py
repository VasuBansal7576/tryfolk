"""Owner connections at the HTTP boundary; external manifests below are fixtures."""

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
from threading import Thread

from tryfolk.config import Target
from tryfolk.target_provenance import dodo_provenance

from fastapi.testclient import TestClient

from tryfolk.api import create_app
from tryfolk.config import Settings

ORIGIN = "http://127.0.0.1:8000"


def owner(client):
    response = client.post("/owner/session", json={"secret": "fixture-owner"}, headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def test_connections_require_owner_session_and_mutations_require_csrf(tmp_path):
    client = TestClient(create_app(Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner")))
    assert client.get("/connections").status_code == 401
    assert client.post("/connections", json={"app_url": "http://127.0.0.1:3339"}).status_code == 401
    assert client.get("/connections", headers={"Authorization": "Bearer fixture-owner"}).status_code == 401
    headers = owner(client)
    response = client.get("/connections")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "version": "1",
        "registration_supported": True,
        "ownership_scope": "owner-approved-loopback-dodo-test",
        "supported_adapters": ["owned-dodo-special-downloads-v1"],
        "targets": [],
    }
    assert client.post("/connections", json={"app_url": "http://127.0.0.1:3339"}).status_code == 403
    bad = headers | {"Origin": "https://evil.example"}
    assert (
        client.post("/connections", json={"app_url": "http://127.0.0.1:3339"}, headers=bad).status_code == 403
    )
    denied = client.post("/connections", json={"app_url": "http://127.0.0.1:3339"}, headers=headers)
    assert denied.status_code == 422
    assert denied.json()["detail"]["code"] == "connection_unsupported"


# This server and source repository are invented compatibility fixtures.
# They provide no genuine Dodo provider or checkout evidence.

FIXTURE_DIGEST = "9d5240ce38dd3dbebd3fa537f8b24ab4b8d2c49af212c10fd18b8fbf1f1a22bb"


@contextmanager
def compatible_target(tmp_path):
    source = tmp_path / "fixture-source"
    (source / "app").mkdir(parents=True)
    (source / "lib").mkdir()
    (source / "app/page.tsx").write_text("// Fixture-owned app. No payment evidence.\n")
    (source / "lib/fulfillment-policy.ts").write_text("// Fixture policy.\n")
    (source / "package.json").write_text('{"name":"connection-fixture","private":true}\n')
    shutil.copyfile(Path(__file__).resolve().parents[1] / "dodo-target/LICENSE.md", source / "LICENSE.md")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Connection Fixture",
            "-c",
            "user.email=fixture@tryfolk.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "Invented connection source fixture",
        ],
        check=True,
    )
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    requests = []
    payload = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path))
            if self.path != "/api/target/readiness":
                self.send_error(404)
                return
            body = payload["_raw"].encode() if "_raw" in payload else json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append((self.command, self.path))
            self.send_error(405)

        def log_message(self, *args):
            pass

    for port in range(3380, 3400):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError("No free fixture port in the owned Dodo range")
    origin = f"http://127.0.0.1:{server.server_port}"
    manifest = {
        "contract_version": "1",
        "target_id": "tryfolk-owned-dodo-special-downloads",
        "upstream_commit": "ef7cbd980d8f1e57b036dc2af71ff05521b6175a",
        "source_commit": commit,
        "source_sha256": FIXTURE_DIGEST,
        "source_clean": True,
        "target_revision": "sha256:" + "c" * 64,
        "provider_environment": "test_mode",
        "mode": "healthy",
        "fault": None,
        "repair": {"source_file": "lib/fulfillment-policy.ts", "disposition": "granted"},
        "app_origin": origin,
        "safe_origins": [origin],
        "checkout_origins": ["https://test.checkout.dodopayments.com"],
        "fixture_auth_enabled": True,
        "adapter": "persistent-local-sqlite",
        "product": {"type": "one-time"},
    }
    payload.update(
        {
            "manifest": manifest,
            "ready": False,
            "provider_baseline_verified": False,
            "evidence_source": "dodo_test_api",
            "errors": [{"code": "missing_write_key"}],
        }
    )
    target = Target(
        url=origin,
        revision=manifest["target_revision"],
        provider="dodo",
        mode="healthy",
        repo_path=str(source),
        allowed_origins=(origin,),
        source_commit=commit,
        source_digest=FIXTURE_DIGEST,
        target_provenance=dodo_provenance(manifest, source),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield target, payload, requests, source
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_verified_registration_persists_pins_and_is_idempotent_without_starting_execution(tmp_path):
    with compatible_target(tmp_path) as (target, payload, requests, source):
        settings = Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
        client = TestClient(create_app(settings))
        headers = owner(client)
        initial = client.get("/connections").json()["targets"]
        assert initial[0]["registration_status"] == "unregistered"
        first = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert first.status_code == 200, first.text
        registered = first.json()
        assert registered["registration_status"] == "registered"
        assert registered["source"]["status"] == "verified"
        assert registered["source"]["commit"] == target.source_commit
        assert registered["source"]["digest"] == FIXTURE_DIGEST
        assert registered["execution_ready"] is False
        assert {g["code"] for g in registered["gates"]} >= {
            "missing_write_key",
            "dodo_runtime_validation",
            "sdk_validation",
            "cfo_plan_missing",
        }
        assert str(source) not in first.text
        assert client.post("/connections", json={"app_url": target.url}, headers=headers).json() == registered
        restarted = TestClient(create_app(settings))
        owner(restarted)
        assert restarted.get("/connections").json()["targets"] == [registered]
        assert all(request == ("GET", "/api/target/readiness") for request in requests)
        quote = client.post("/quotes", json={"app_url": target.url, "persona_count": 1}).json()
        assert quote["available_microusd"] == 1_000_000
        assert quote["ready"] is False


def test_source_changes_revoke_verified_display_without_overwriting_registration(tmp_path):
    with compatible_target(tmp_path) as (target, payload, requests, source):
        client = TestClient(
            create_app(
                Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
            )
        )
        headers = owner(client)
        registered = client.post("/connections", json={"app_url": target.url}, headers=headers).json()
        (source / "app/page.tsx").write_text("// Changed after registration.\n")
        current = client.get("/connections").json()["targets"][0]
        assert current["registration_status"] == "registered"
        assert current["source"]["status"] == "stale"
        assert current["execution_ready"] is False
        assert current["source"]["commit"] == registered["source"]["commit"]
        assert current["gates"][0]["code"] == "connection_source_stale"
        denied = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert denied.status_code == 422
        assert denied.json()["detail"]["code"] == "connection_provenance"
        assert requests == [("GET", "/api/target/readiness")]


def test_malformed_target_response_is_actionable_and_never_saved(tmp_path):
    with compatible_target(tmp_path) as (target, payload, requests, source):
        client = TestClient(
            create_app(
                Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
            )
        )
        headers = owner(client)
        payload["_raw"] = "not-json and no evidence"
        response = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "connection_provenance"
        assert "not-json" not in response.text
        assert client.get("/connections").json()["targets"][0]["registration_status"] == "unregistered"


def test_current_owner_prerequisite_gates_replace_old_snapshot_without_regranting_execution(tmp_path):
    with compatible_target(tmp_path) as (target, payload, requests, source):
        settings = Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
        client = TestClient(create_app(settings))
        headers = owner(client)
        assert client.post("/connections", json={"app_url": target.url}, headers=headers).status_code == 200
        from dataclasses import replace

        changed = replace(
            settings,
            gates=({"code": "owner_review", "message": "Owner review pending.", "action": "Finish review."},),
        )
        restarted = TestClient(create_app(changed))
        owner(restarted)
        current = restarted.get("/connections").json()["targets"][0]
        codes = {g["code"] for g in current["gates"]}
        assert "owner_review" in codes
        assert "sdk_validation" not in codes
        assert current["source"]["status"] == "verified"
        assert current["execution_ready"] is False
        assert requests == [("GET", "/api/target/readiness")]


def test_real_json_registry_shape_registers_without_accepting_browser_source_overrides(tmp_path, monkeypatch):
    from dataclasses import asdict

    with compatible_target(tmp_path) as (target, payload, requests, source):
        registry = tmp_path / "connections.json"
        registry.write_text(json.dumps([asdict(target)]))
        monkeypatch.setenv("TRYFOLK_CONNECTIONS", str(registry))
        monkeypatch.setenv("TRYFOLK_DB", str(tmp_path / "db.sqlite"))
        monkeypatch.setenv("TRYFOLK_OWNER_TOKEN", "fixture-owner")
        client = TestClient(create_app(Settings.from_env()))
        headers = owner(client)
        rejected = client.post(
            "/connections", json={"app_url": target.url, "repo_path": "/etc"}, headers=headers
        )
        assert rejected.status_code == 422
        assert requests == []
        accepted = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["source"]["status"] == "verified"


def test_unknown_setup_code_cannot_serialize_private_target_strings(tmp_path):
    with compatible_target(tmp_path) as (target, payload, requests, source):
        client = TestClient(
            create_app(
                Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
            )
        )
        headers = owner(client)
        payload["errors"] = [{"code": "synthetic_private_value", "action": "synthetic_private_detail"}]
        response = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert response.status_code == 422
        assert "synthetic_private" not in response.text
        assert client.get("/connections").json()["targets"][0]["registration_status"] == "unregistered"


def test_unapproved_urls_and_client_authority_fields_never_contact_target(tmp_path):
    with compatible_target(tmp_path) as (target, payload, requests, source):
        client = TestClient(
            create_app(
                Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
            )
        )
        headers = owner(client)
        for url in [
            "http://169.254.169.254",
            "https://test.dodopayments.com",
            "file:///etc/passwd",
            "http://localhost:3380",
            target.url + "/other",
            target.url + "?key=synthetic-private",
            target.url.replace("127.0.0.1", "user@127.0.0.1"),
            "http://127.0.0.1:3300",
        ]:
            response = client.post("/connections", json={"app_url": url}, headers=headers)
            assert response.status_code == 422
            assert "synthetic-private" not in response.text
        for key in ["source_commit", "source_digest", "revision", "provider", "execution_ready", "api_key"]:
            response = client.post(
                "/connections", json={"app_url": target.url, key: "client-choice"}, headers=headers
            )
            assert response.status_code == 422
        assert requests == []
        assert client.get("/connections").json()["targets"][0]["registration_status"] == "unregistered"


def test_wrong_build_origin_provider_or_fixture_receipts_cannot_register(tmp_path):
    from copy import deepcopy

    with compatible_target(tmp_path) as (target, payload, requests, source):
        client = TestClient(
            create_app(
                Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
            )
        )
        headers = owner(client)
        baseline = deepcopy(payload)
        mutations = [
            {"app_origin": "https://unapproved.example"},
            {"safe_origins": [target.url, "https://extra.example"]},
            {"checkout_origins": ["https://checkout.dodopayments.com"]},
            {"provider_environment": "live_mode"},
            {"source_commit": "d" * 40},
            {"source_sha256": "e" * 64},
            {"source_clean": False},
            {"target_revision": "sha256:" + "f" * 64},
            {"mode": "broken"},
            {"upstream_commit": "a" * 40},
            {"fixture_auth_enabled": False},
            {"adapter": "arbitrary-adapter"},
            {"product": {"type": "subscription"}},
        ]
        for mutation in mutations:
            payload.clear()
            payload.update(deepcopy(baseline))
            payload["manifest"].update(mutation)
            denied = client.post("/connections", json={"app_url": target.url}, headers=headers)
            assert denied.status_code == 422, mutation
            assert denied.json()["detail"]["code"] == "connection_provenance"
        payload.clear()
        payload.update(baseline)
        payload["evidence_source"] = "external_provider_fixture"
        assert client.post("/connections", json={"app_url": target.url}, headers=headers).status_code == 422
        assert client.get("/connections").json()["targets"][0]["registration_status"] == "unregistered"
        assert all(request == ("GET", "/api/target/readiness") for request in requests)


def test_ready_target_configuration_does_not_clear_independent_execution_gate(tmp_path):
    with compatible_target(tmp_path) as (target, payload, requests, source):
        payload.update({"ready": True, "errors": [], "provider_baseline_verified": True})
        client = TestClient(
            create_app(
                Settings(
                    db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,), gates=()
                )
            )
        )
        headers = owner(client)
        response = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert response.status_code == 200
        assert response.json()["source"]["status"] == "verified"
        assert response.json()["execution_ready"] is False
        assert "dodo_runtime_validation" in {g["code"] for g in response.json()["gates"]}


def test_plan_file_presence_cannot_create_false_execution_readiness(tmp_path):
    from dataclasses import replace

    with compatible_target(tmp_path) as (target, payload, requests, source):
        target = replace(target, execution_ready=True)
        payload.update({"ready": True, "errors": []})
        plan = tmp_path / "invalid-plan.json"
        plan.write_text("{}")
        client = TestClient(
            create_app(
                Settings(
                    db_path=tmp_path / "db.sqlite",
                    owner_token="fixture-owner",
                    targets=(target,),
                    gates=(),
                    cfo_plan_path=plan,
                )
            )
        )
        headers = owner(client)
        response = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert response.status_code == 200
        assert response.json()["execution_ready"] is False
        assert "cfo_plan_invalid" in {g["code"] for g in response.json()["gates"]}


def test_ignored_source_cannot_claim_a_commit_that_does_not_contain_the_build(tmp_path):
    from dataclasses import replace

    with compatible_target(tmp_path) as (target, payload, requests, source):
        subprocess.run(["git", "-C", str(source), "rm", "--cached", "-q", "app/page.tsx"], check=True)
        (source / ".gitignore").write_text("app/page.tsx\n")
        subprocess.run(["git", "-C", str(source), "add", ".gitignore"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Connection Fixture",
                "-c",
                "user.email=fixture@tryfolk.invalid",
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-qm",
                "Fixture commit omits a build input",
            ],
            check=True,
        )
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        payload["manifest"]["source_commit"] = commit
        target = replace(
            target, source_commit=commit, target_provenance=dodo_provenance(payload["manifest"], source)
        )
        client = TestClient(
            create_app(
                Settings(db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,))
            )
        )
        headers = owner(client)
        denied = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert denied.status_code == 422
        assert denied.json()["detail"]["code"] == "connection_provenance"
        assert requests == []


def test_string_runtime_flag_cannot_count_as_independent_execution_approval(tmp_path):
    from dataclasses import replace

    with compatible_target(tmp_path) as (target, payload, requests, source):
        target = replace(target, execution_ready="false")
        payload.update({"ready": True, "errors": []})
        client = TestClient(
            create_app(
                Settings(
                    db_path=tmp_path / "db.sqlite", owner_token="fixture-owner", targets=(target,), gates=()
                )
            )
        )
        headers = owner(client)
        response = client.post("/connections", json={"app_url": target.url}, headers=headers)
        assert response.status_code == 200
        assert "dodo_runtime_validation" in {g["code"] for g in response.json()["gates"]}
        assert response.json()["execution_ready"] is False
