"""Offline association validation; these fixtures are not native incident evidence."""

import json

import pytest

from test_api import auth, client_at, quote, start


def setup_incident(tmp_path, monkeypatch):
    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(tmp_path))
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    monkeypatch.delenv("TRYFOLK_NEATLOGS_ENV", raising=False)
    client = client_at(tmp_path / "runs.sqlite")
    run = start(client, quote(client)).json()
    trace_id = "8" * 32
    with client.app.state.service.store.transaction() as db:
        db.execute(
            "INSERT INTO events(run_id,persona_id,kind,created_at,data,trace_url) VALUES (?,?,?,?,?,?)",
            (run["id"], "3" * 32, "verification", "now", "{}",
             "https://app.neatlogs.com/traces/" + trace_id),
        )
    incident_id = "10000000-0000-4000-8000-000000000001"
    item = {
        "run_id": run["id"], "incident_id": incident_id, "source": "official_neatlogs_ui",
        "scope": "current_run", "status": "open", "trace_ids": [trace_id],
        "provider": "local-fixture",
        "viewer_url": "https://app.neatlogs.com/incidents/" + incident_id,
        "observed_at": "2026-10-13T00:00:00Z", "resolution_evidence": "Investigation pending.",
    }
    path = tmp_path / (run["id"] + "-neatlogs-incident.json")
    path.write_text(json.dumps(item))
    return client, run, trace_id, item, path


def test_explicit_incident_observation_is_owned_and_preserves_vendor_state(tmp_path, monkeypatch):
    client, run, trace_id, item, _ = setup_incident(tmp_path, monkeypatch)
    route = f"/runs/{run['id']}/traces"
    assert client.get(route).status_code == 401
    result = client.get(route, headers=auth(run))
    assert result.json()["incidents"] == [item]
    assert "no-store" in result.headers["cache-control"]
    inspection = client.get(route + "/" + trace_id, headers=auth(run)).json()
    assert inspection["incidents"] == [item]
    assert inspection["finalized_readback"] == "not_verified"


@pytest.mark.parametrize("fault", ["missing", "wrong_run", "wrong_host", "wrong_id", "wrong_trace", "local_source", "oversized", "malformed"])
def test_invalid_or_unassociated_incident_creates_no_claim(tmp_path, monkeypatch, fault):
    client, run, _, item, path = setup_incident(tmp_path, monkeypatch)
    if fault == "missing":
        path.unlink()
    elif fault == "malformed":
        path.write_text("{")
    elif fault == "oversized":
        path.write_text(" " * 100_001)
    else:
        if fault == "wrong_run":
            item["run_id"] = "9" * 32
        elif fault == "wrong_host":
            item["viewer_url"] = item["viewer_url"].replace("app.neatlogs.com", "example.com")
        elif fault == "wrong_id":
            item["incident_id"] = "20000000-0000-4000-8000-000000000001"
        elif fault == "wrong_trace":
            item["trace_ids"] = ["9" * 32]
        elif fault == "local_source":
            item["source"] = "local_failure_bucket"
        path.write_text(json.dumps(item))
    result = client.get(f"/runs/{run['id']}/traces", headers=auth(run)).json()
    assert result["incidents"] == []


def test_prior_incident_is_separate_and_never_assigned_to_new_persona(tmp_path, monkeypatch):
    client, run, trace_id, item, path = setup_incident(tmp_path, monkeypatch)
    item.update(scope="prior_supporting_incident", trace_ids=["9" * 32], status="resolved")
    path.write_text(json.dumps(item))
    route = f"/runs/{run['id']}/traces"
    assert client.get(route, headers=auth(run)).json()["incidents"] == [item]
    assert client.get(route + "/" + trace_id, headers=auth(run)).json()["incidents"] == []
