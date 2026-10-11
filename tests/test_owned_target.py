"""Public fixture HTTP behavior; only external provider transport is replaced."""

import importlib.util
from pathlib import Path
from fastapi.testclient import TestClient


def load_target(monkeypatch, root, role, source):
    monkeypatch.setenv("TARGET_STATE", str(root))
    monkeypatch.setenv("TARGET_ROLE", role)
    monkeypatch.setenv("TARGET_FIXTURE_SECRET", "fixture-key")
    monkeypatch.setenv("TARGET_FULFILLMENT", str(source))
    spec = importlib.util.spec_from_file_location(
        "target_" + role, Path(__file__).parents[1] / "owned-target/fixture/app.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_idempotency_and_read_only_entitlement_under_isolated_revisions(tmp_path, monkeypatch):
    healthy = tmp_path / "healthy.py"
    healthy.write_text('def should_grant(payment):\n    return payment["status"] == "succeeded"\n')
    broken = tmp_path / "broken.py"
    broken.write_text("def should_grant(payment):\n    return False\n")
    provider = load_target(monkeypatch, tmp_path / "provider", "provider", healthy)
    provider_client = TestClient(provider.app)
    header = {"Authorization": "Bearer fixture-key"}

    class ProviderTransport:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            return provider_client.post("/payments", **kwargs)

    for label, source, expected in [("broken", broken, False), ("healthy", healthy, True)]:
        target = load_target(monkeypatch, tmp_path / label, "app", source)
        client = TestClient(target.app)
        monkeypatch.setattr(target.httpx, "Client", ProviderTransport)
        seed = client.post(
            "/fixtures/accounts",
            json={"run_id": label, "persona_id": "same-p", "conditions": {"history": "new"}},
            headers=header,
        ).json()
        path = "/entitlements/" + seed["account_id"]
        initial = client.get(path, headers=header).json()
        assert initial["granted"] is False and initial["units"] == 0
        client.get(seed["session_url"])
        assert client.post("/checkout").status_code == 200
        payment = provider_client.get("/payments/" + seed["checkout_id"], headers=header).json()
        assert payment["status"] == "succeeded" and payment["account_id"] == seed["account_id"]
        entitlement = client.get(path, headers=header).json()
        assert entitlement["granted"] is expected
        assert client.get(path, headers=header).json() == entitlement
        client.post("/checkout")
        assert provider_client.get("/payments/" + seed["checkout_id"], headers=header).json() == payment
        assert client.get(path, headers=header).json() == entitlement
        assert entitlement["units"] == (10 if expected else 0)
