"""Owned subprocess lifecycle with a labelled unit HTTP fixture, no SDK/provider I/O."""

import json
import time
import pytest
from tryfolk.dodo_previews import DodoPreviewRegistry, DodoPreviewSupervisor
from tryfolk.source_workspace import OwnedSource, source_fingerprint
from tryfolk.store import Store
from test_source_workspace import baseline


def registered(tmp_path):
    root, target = baseline(tmp_path)
    workspace = tmp_path / "repairs/r/source"
    pin = OwnedSource(root).snapshot(workspace, expected_digest=source_fingerprint(target))
    (workspace / ".next").mkdir()
    (workspace / ".next/BUILD_ID").write_text("unit-fixture-not-a-next-build")
    (workspace / ".gitignore").write_text((workspace / ".gitignore").read_text() + ".target-manifest.json\n")
    from tryfolk.source_workspace import git

    git(workspace, "add", ".gitignore")
    git(
        workspace,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "unit build ignores",
    )
    pin["base_commit"] = git(workspace, "rev-parse", "HEAD")
    built = {"source_sha256": pin["source_digest"], "source_commit": pin["base_commit"], "source_clean": True}
    (workspace / ".target-manifest.json").write_text(json.dumps(built))
    store = Store(tmp_path / "state.sqlite")
    registry = DodoPreviewRegistry(store, tmp_path / "repairs", range(3388, 3389))
    params = {
        "source": workspace,
        "source_commit": pin["base_commit"],
        "source_digest": pin["source_digest"],
        "mode": "broken",
        "run_id": "unit-run",
    }
    return registry, workspace, params


def test_registered_source_and_build_are_immutable_with_no_client_origin_override(tmp_path):
    registry, workspace, params = registered(tmp_path)
    row = registry.request("r", **params)
    assert row["origin"] == "http://127.0.0.1:3388"
    assert registry.request("r", **params) == row
    with pytest.raises(ValueError):
        registry.request("r", **{**params, "mode": "healthy"})
    with pytest.raises(ValueError):
        registry.request("r", **{**params, "source": tmp_path})
    with pytest.raises(TypeError):
        registry.request("r", **params, origin="https://unowned.example")
    (workspace / "lib/fulfillment-policy.ts").write_text("mutated source")
    with pytest.raises(ValueError):
        registry.request("r", **params)


def test_supervisor_retains_its_process_then_cancellation_reaps_only_that_child(tmp_path):
    registry, workspace, params = registered(tmp_path)
    row = registry.request("r", **params)
    manifest = {
        "source_commit": params["source_commit"],
        "source_sha256": params["source_digest"],
        "source_clean": True,
        "app_origin": row["origin"],
        "safe_origins": [row["origin"]],
        "checkout_origins": ["https://test.checkout.dodopayments.com"],
        "mode": "broken",
        "provider_environment": "test_mode",
        "target_revision": "sha256:unit-fixture",
    }
    entry = workspace / "node_modules/next/dist/bin/next"
    entry.parent.mkdir(parents=True)
    entry.write_text(
        "require('node:http').createServer((q,r)=>{r.setHeader('Content-Type','application/json');r.end("
        + json.dumps(json.dumps({"ready": True, "manifest": manifest}))
        + ");}).listen(3388,'127.0.0.1');"
    )
    supervisor = DodoPreviewSupervisor(registry, {})
    try:
        deadline = time.monotonic() + 10
        while registry.get("r")["status"] != "ready" and time.monotonic() < deadline:
            supervisor.tick()
            time.sleep(0.05)
        assert registry.get("r")["status"] == "ready", registry.get("r")
        child = supervisor.children["r"]
        assert child.poll() is None
        with registry.store.transaction() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?)",
                ("unit-run", json.dumps({"id": "unit-run", "phase": "cancelled"}), "unit-only"),
            )
        supervisor.tick()
        assert registry.get("r")["status"] == "stopped"
        assert child.poll() is not None
    finally:
        supervisor.close()
