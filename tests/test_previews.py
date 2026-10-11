"""Real owned subprocess lifetime through the durable registration interface."""

import hashlib
import secrets
import time
from pathlib import Path
import httpx
import pytest
from tryfolk.previews import PreviewRegistry, PreviewSupervisor
from tryfolk.store import Store


def test_registered_preview_survives_request_return_and_supervisor_reaps_only_its_child(tmp_path):
    source = tmp_path / "repairs" / "repair-1" / "source" / "fulfillment.py"
    source.parent.mkdir(parents=True)
    source.write_text('def should_grant(payment):\n    return payment.get("status") == "succeeded"\n')
    store = Store(tmp_path / "runtime.sqlite")
    registry = PreviewRegistry(store, tmp_path / "repairs", port_range=range(3188, 3189))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    first = registry.request("repair-1", source=source, revision="test-exact-revision", source_digest=digest)
    assert (
        registry.request("repair-1", source=source, revision="test-exact-revision", source_digest=digest)[
            "id"
        ]
        == first["id"]
    )
    with pytest.raises(ValueError):
        registry.request("repair-1", source=source, revision="different", source_digest=digest)
    supervisor = PreviewSupervisor(
        registry,
        fixture_secret=secrets.token_urlsafe(16),
        app_dir=Path(__file__).parents[1] / "owned-target/fixture",
    )
    try:
        deadline = time.monotonic() + 10
        while registry.get(first["id"])["status"] != "ready" and time.monotonic() < deadline:
            supervisor.tick()
            time.sleep(0.05)
        ready = registry.get(first["id"])
        assert ready["status"] == "ready", ready
        # Registration returned before startup. Its caller owns no process handle.
        time.sleep(0.1)
        for _ in range(3):
            supervisor.tick()
            response = httpx.get(ready["origin"] + "/health", trust_env=False)
            assert response.json()["source_digest"] == digest
            assert response.json()["revision"] == "test-exact-revision"
        assert supervisor.children[first["id"]].poll() is None
        source.write_text("def should_grant(payment):\n    return False\n")
        supervisor.tick()
        assert registry.get(first["id"])["status"] == "failed"
        assert supervisor.children[first["id"]].poll() is not None
    finally:
        supervisor.close()
