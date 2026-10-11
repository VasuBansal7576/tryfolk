"""Unit external results through owner/public run interfaces; no live provider claims."""

import json
import pytest
from fastapi.testclient import TestClient
from tryfolk.api import create_app
from tryfolk.config import Settings, Target
from tryfolk.service import DomainError


def completed_baseline(tmp_path, target_provenance=None):
    app = create_app(
        Settings(
            db_path=tmp_path / "db.sqlite",
            owner_token="unit-owner",
            gates=(),
            targets=(
                Target(
                    url="http://127.0.0.1:3301",
                    provider="dodo",
                    revision="sha256:" + "a" * 64,
                    fault="controlled",
                    repo_path=str(tmp_path),
                    target_provenance=target_provenance,
                ),
            ),
        )
    )
    client = TestClient(app)
    q = client.post("/quotes", json={"app_url": "http://127.0.0.1:3301", "persona_count": 2}).json()
    run = client.post("/runs", json={"quote_id": q["id"]}, headers={"Idempotency-Key": "unit-start"}).json()
    service = app.state.service
    with service.store.transaction() as db:
        before = service.store.load(db, "runs", run["id"])
        before["phase"] = "completed"
        service.store.save(db, "runs", before)
        db.execute(
            "UPDATE populations SET payload=? WHERE id=?",
            (json.dumps([{"id": "unit-person-1"}, {"id": "unit-person-2"}]), before["population_id"]),
        )
        finding = {
            "id": "unit-finding",
            "run_id": run["id"],
            "persona_id": "unit-person-1",
            "measured": True,
            "evidence": {"source": "unit fixture, not live"},
        }
        db.execute("INSERT INTO findings VALUES (?,?,?)", (finding["id"], run["id"], json.dumps(finding)))
    repair = service.repair(run["id"], "unit-finding", "unit-owner")
    return client, service, run, repair


def test_dodo_replay_pins_target_revision_distinct_from_source_head_and_original_population(tmp_path):
    # Invented immutable provenance fixture: never a live licensed-source claim.
    provenance = {"controlled_fulfillment_defect":True, "baseline_target_revision":"sha256:" + "a"*64}
    client, service, run, repair = completed_baseline(tmp_path, provenance)
    service.register_repair(
        repair["id"],
        head_revision="b" * 40,
        source_digest="c" * 64,
        target_revision="sha256:" + "d" * 64,
        preview_url="http://127.0.0.1:3310",
        checkpoint_id="01M4KFBGMCS6DE9CSZVNDTZ05J",
        checkpoint_url=None,
        provider="dodo",
    )
    response = client.post(
        f"/runs/{run['id']}/replay",
        json={"repair_id": repair["id"]},
        headers={"Authorization": "Bearer unit-owner", "Idempotency-Key": "unit-replay"},
    )
    assert response.status_code == 201, response.text
    after = response.json()["status"]
    assert after["revision"] == "sha256:" + "d" * 64
    assert after["source_commit"] == "b" * 40 and after["source_digest"] == "c" * 64
    assert after["persona_count"] == 2 and after["population_id"] == run["status"]["population_id"]
    assert after["budget_contract"] == run["status"]["budget_contract"]
    assert after["target_provenance"] == provenance == run["status"]["target_provenance"]
    assert after["target_provenance"]["baseline_target_revision"] != after["revision"]
    with service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 1


def test_provider_origin_and_source_metadata_cannot_be_swapped_at_worker_registration(tmp_path):
    _, service, _, repair = completed_baseline(tmp_path)
    valid = {
        "head_revision": "b" * 40,
        "source_digest": "c" * 64,
        "target_revision": "sha256:" + "d" * 64,
        "preview_url": "http://127.0.0.1:3310",
        "checkpoint_id": "01M4KFBGMCS6DE9CSZVNDTZ05J",
        "checkpoint_url": None,
        "provider": "dodo",
    }
    for changes in (
        {"provider": "local-fixture"},
        {"preview_url": "http://127.0.0.1:3105"},
        {"preview_url": "http://u:p@127.0.0.1:3310"},
        {"preview_url": "http://127.0.0.1:3310/?unregistered=true"},
        {"target_revision": "arbitrary"},
        {"source_digest": None},
    ):
        with pytest.raises(DomainError):
            service.register_repair(repair["id"], **(valid | changes))
