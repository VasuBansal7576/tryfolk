"""Offline HTTP/capture fixtures only; no genuine provider or Entire capture claims."""

from test_api import auth, client_at, quote, start
from dataclasses import replace
import hashlib
import hmac
import json
import os
import subprocess

import httpx
import pytest
from fastapi.testclient import TestClient

from test_dodo_revision import completed_baseline
from tryfolk.api import create_app
from tryfolk.repair import checkpoint_commit


CHECKPOINT = "01M4KENRAJQXV9A4G8B7H478JF"
SESSION = "11111111-2222-4333-8444-555555555555"
REPOSITORY = "OfflineOwner/repair-demo"


def captured_repair(tmp_path, repository=REPOSITORY, response_headers=None, metadata_path="0/metadata.json"):
    """Invented local capture and public HTTP objects, never a live Entire receipt."""
    from tryfolk.checkpoint_access import seal_capture_binding

    _, old_service, run, repair = completed_baseline(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    workspace = tmp_path / "source"
    (workspace / "lib").mkdir(parents=True)
    env = os.environ | {
        "GIT_AUTHOR_NAME": "Offline Fixture",
        "GIT_COMMITTER_NAME": "Offline Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@tryfolk.invalid",
        "GIT_COMMITTER_EMAIL": "fixture@tryfolk.invalid",
    }

    def git(*args, data=None):
        return subprocess.run(
            ["git", "-C", str(workspace), *args],
            input=data,
            env=env,
            text=True,
            check=True,
            capture_output=True,
        ).stdout.strip()

    git("init", "-b", "offline-fixture")
    source = workspace / "lib/fulfillment-policy.ts"
    source.write_text("export function disposition() { return 'suppressed'; }\n")
    git("add", ".")
    git("commit", "-m", "Offline broken source fixture")
    source.write_text("export function disposition() { return 'granted'; }\n")
    git("add", ".")
    git("commit", "-m", "Offline source repair fixture")
    capture = git("rev-parse", "HEAD")
    registered = checkpoint_commit(workspace, CHECKPOINT)
    transcript = (
        json.dumps(
            {"type": "session_meta", "payload": {"id": SESSION, "cwd": str(workspace), "fixture_only": True}}
        )
        + "\n"
    ).encode()
    files = {
        "metadata.json": json.dumps(
            {
                "checkpoint_id": CHECKPOINT,
                "sessions": [{"metadata": metadata_path, "transcript": "0/transcript.jsonl"}],
            }
        ).encode(),
        "0/metadata.json": json.dumps(
            {"checkpoint_id": CHECKPOINT, "session_id": SESSION, "agent": "Codex"}
        ).encode(),
        "0/full.jsonl": transcript,
        "0/transcript.jsonl": b'{"fixture_only":"compact, not the raw capture"}\n',
    }
    blobs = {path: git("hash-object", "-w", "--stdin", data=data.decode()) for path, data in files.items()}
    subtree = git(
        "mktree",
        data="".join(
            f"100644 blob {blobs['0/' + name]}\t{name}\n"
            for name in ["full.jsonl", "metadata.json", "transcript.jsonl"]
        ),
    )
    tree = git(
        "mktree", data=f"040000 tree {subtree}\t0\n100644 blob {blobs['metadata.json']}\tmetadata.json\n"
    )
    checkpoint_object = git("commit-tree", tree, "-m", "Offline checkpoint object fixture")
    git("update-ref", "refs/entire/checkpoints/JF/" + CHECKPOINT, checkpoint_object)
    state_dir = workspace / ".git/entire-sessions"
    state_dir.mkdir()
    (state_dir / (SESSION + ".json")).write_text(
        json.dumps(
            {
                "session_id": SESSION,
                "last_checkpoint_id": CHECKPOINT,
                "agent_type": "Codex",
                "base_commit": capture,
            }
        )
    )
    prefix = evidence / repair["id"]
    prefix.with_name(prefix.name + "-checkpoint-association.json").write_text(
        json.dumps(
            {
                "capture_commit": capture,
                "registered_commit": registered,
                "checkpoint_id": CHECKPOINT,
                "session_id": SESSION,
                "transcript_sha256": hashlib.sha256(transcript).hexdigest(),
                "source_unchanged_by_trailer": True,
            }
        )
    )
    seal_capture_binding(
        workspace,
        prefix,
        run_id=run["id"],
        repair_id=repair["id"],
        source_digest="c" * 64,
        owner_token="unit-owner",
        repository=repository,
    )
    files["source"] = source.read_bytes()
    requested = []

    def public_http(request):
        assert request.method == "GET" and request.url.host == "raw.githubusercontent.com"
        assert "authorization" not in request.headers and "cookie" not in request.headers
        requested.append(str(request.url))
        checkpoint_prefix = f"/{repository}/{checkpoint_object}/"
        if request.url.path.startswith(checkpoint_prefix):
            data = files[request.url.path.removeprefix(checkpoint_prefix)]
        else:
            assert request.url.path == f"/{repository}/{registered}/lib/fulfillment-policy.ts"
            data = files["source"]
        return httpx.Response(200, content=data, headers=response_headers)

    settings = replace(
        old_service.settings, checkpoint_repository=repository, checkpoint_evidence_root=evidence
    )
    app = create_app(settings, checkpoint_transport=httpx.MockTransport(public_http))
    client = TestClient(app)
    app.state.service.register_repair(
        repair["id"],
        head_revision=registered,
        source_digest="c" * 64,
        target_revision="sha256:" + "d" * 64,
        preview_url="http://127.0.0.1:3310",
        checkpoint_id=CHECKPOINT,
        checkpoint_url=None,
        provider="dodo",
    )
    return client, run, repair, files, requested, settings, checkpoint_object, registered


def test_only_owner_can_finalize_a_runs_checkpoint_access(tmp_path):
    client = client_at(tmp_path / "runs.sqlite")
    run = start(client, quote(client)).json()
    url = f"/runs/{run['id']}/repairs/{'a' * 32}/checkpoint-access"
    assert client.post(url, json={}).status_code == 403
    assert client.post(url, json={}, headers=auth(run)).status_code == 403


def test_a_checkpoint_root_for_another_session_cannot_create_authenticated_capture_evidence(tmp_path):
    from tryfolk.checkpoint_access import CheckpointAccessError

    with pytest.raises(CheckpointAccessError, match="checkpoint_capture_mismatch"):
        captured_repair(tmp_path, metadata_path="1/metadata.json")


def test_owner_finalizes_actual_resource_bytes_and_persists_the_same_repair(tmp_path):
    client, run, repair, _, _, settings, obj, source = captured_repair(tmp_path)
    endpoint = f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access"
    response = client.post(endpoint, json={}, headers={"Authorization": "Bearer unit-owner"})
    assert response.status_code == 200, response.text
    result = response.json()
    expected = f"https://raw.githubusercontent.com/{REPOSITORY}/{obj}/metadata.json"
    assert result["checkpoint_id"] == CHECKPOINT and result["checkpoint_url"] == expected
    assert result["checkpoint_access"]["kind"] == "github_git_checkpoint"
    assert result["checkpoint_access"]["source_commit"] == source
    assert result["checkpoint_access"]["hosted_entire_status"] == "unverified"
    assert result["checkpoint_access"]["hosted_entire_url"] is None
    restarted = TestClient(create_app(settings))
    saved = restarted.get(f"/runs/{run['id']}", headers={"Authorization": "Bearer unit-owner"}).json()
    assert saved["repair"]["checkpoint_url"] == expected
    assert saved["repair"]["id"] == repair["id"] and saved["revision"] == run["status"]["revision"]
    assert saved["repair"]["preview_url"] == "http://127.0.0.1:3310"


def test_public_evidence_reads_remain_anonymous_when_a_response_sets_a_cookie(tmp_path):
    client, run, repair, *_ = captured_repair(
        tmp_path, response_headers={"Set-Cookie": "offline_session=fake; Path=/"}
    )
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 200, response.text


def test_signed_checkpoint_object_must_match_its_bound_resource_objects(tmp_path):
    client, run, repair, _, requested, settings, *_ = captured_repair(tmp_path)
    path = settings.checkpoint_evidence_root / (repair["id"] + "-checkpoint-access-binding.json")
    envelope = json.loads(path.read_bytes())
    envelope["payload"]["checkpoint_object"] = "f" * 40
    canonical = json.dumps(envelope["payload"], sort_keys=True, separators=(",", ":")).encode()
    envelope["signature"] = hmac.new(
        b"unit-owner", b"tryfolk.checkpoint-publication-binding.v1\0" + canonical, hashlib.sha256
    ).hexdigest()
    path.chmod(0o600)
    path.write_text(json.dumps(envelope))
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 409
    assert not requested


@pytest.mark.parametrize("resource", ["metadata.json", "0/metadata.json", "0/full.jsonl", "source"])
def test_changed_public_checkpoint_session_transcript_or_source_bytes_cannot_persist_a_link(
    tmp_path, resource
):
    client, run, repair, files, *_ = captured_repair(tmp_path)
    files[resource] += b"changed offline bytes"
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 409
    status = client.get(f"/runs/{run['id']}", headers={"Authorization": "Bearer unit-owner"}).json()
    assert status["repair"]["checkpoint_url"] is None
    assert "checkpoint_access" not in status["repair"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "f" * 32),
        ("repair_id", "f" * 32),
        ("checkpoint_id", "01M4KENRAJQXV9A4G8B7H478JG"),
        ("source_commit", "f" * 40),
        ("source_digest", "f" * 64),
    ],
)
def test_even_signed_evidence_for_another_run_checkpoint_or_source_is_rejected(tmp_path, field, value):
    client, run, repair, _, requested, settings, *_ = captured_repair(tmp_path)
    path = settings.checkpoint_evidence_root / (repair["id"] + "-checkpoint-access-binding.json")
    envelope = json.loads(path.read_bytes())
    envelope["payload"][field] = value
    canonical = json.dumps(envelope["payload"], sort_keys=True, separators=(",", ":")).encode()
    envelope["signature"] = hmac.new(
        b"unit-owner", b"tryfolk.checkpoint-publication-binding.v1\0" + canonical, hashlib.sha256
    ).hexdigest()
    path.chmod(0o600)
    path.write_text(json.dumps(envelope))
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 409 and not requested


def test_a_tampered_private_capture_signature_cannot_certify_public_evidence(tmp_path):
    client, run, repair, _, requested, settings, *_ = captured_repair(tmp_path)
    path = settings.checkpoint_evidence_root / (repair["id"] + "-checkpoint-access-binding.json")
    envelope = json.loads(path.read_bytes())
    envelope["signature"] = "0" * 64
    path.chmod(0o600)
    path.write_text(json.dumps(envelope))
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 409 and not requested


@pytest.mark.parametrize(
    "body",
    [
        {"checkpoint_url": "http://169.254.169.254/private"},
        {"repository": "AnotherOwner/other-repository"},
        {"verified": True},
    ],
)
def test_the_client_cannot_select_a_destination_or_supply_success_proof(tmp_path, body):
    client, run, repair, _, requested, *_ = captured_repair(tmp_path)
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json=body,
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 422 and not requested


def test_redirects_to_foreign_or_private_destinations_are_never_followed(tmp_path):
    client, run, repair, *_ = captured_repair(tmp_path)
    seen = []

    def redirect(request):
        seen.append(str(request.url))
        assert request.url.host == "raw.githubusercontent.com"
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/private"})

    client.app.state.service.checkpoint_transport = httpx.MockTransport(redirect)
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 409 and len(seen) == 1


def test_repair_from_another_run_cannot_be_finalized_on_the_requested_run(tmp_path):
    client, run, repair, _, requested, *_ = captured_repair(tmp_path)
    q = client.post("/quotes", json={"app_url": "http://127.0.0.1:3301", "persona_count": 1}).json()
    other = client.post("/runs", json={"quote_id": q["id"]}, headers={"Idempotency-Key": "other"}).json()
    response = client.post(
        f"/runs/{other['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 409 and not requested
    assert (
        client.get(f"/runs/{run['id']}", headers={"Authorization": "Bearer unit-owner"}).json()["repair"][
            "checkpoint_url"
        ]
        is None
    )


def test_another_owner_approved_repository_works_without_a_hardcoded_destination(tmp_path):
    repo = "AnotherOrg/customer-target"
    client, run, repair, _, requested, _, obj, _ = captured_repair(tmp_path, repository=repo)
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 200
    assert (
        response.json()["checkpoint_url"] == f"https://raw.githubusercontent.com/{repo}/{obj}/metadata.json"
    )
    assert all("/AnotherOrg/customer-target/" in url for url in requested)


def test_invalid_or_changed_approved_repository_stops_before_public_reads(tmp_path):
    client, run, repair, _, requested, settings, *_ = captured_repair(tmp_path)
    for repo in (None, "https://foreign.example/owner/repo", "Owner/../private", "OtherOwner/other"):
        app = create_app(
            replace(settings, checkpoint_repository=repo),
            checkpoint_transport=client.app.state.service.checkpoint_transport,
        )
        response = TestClient(app).post(
            f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
            json={},
            headers={"Authorization": "Bearer unit-owner"},
        )
        assert response.status_code == 409 and not requested
        assert response.json()["detail"]["code"] == "checkpoint_repository_unapproved"


def test_owner_cookie_requires_csrf_before_checkpoint_reads(tmp_path):
    client, run, repair, _, requested, *_ = captured_repair(tmp_path)
    login = client.post(
        "/owner/session", json={"secret": "unit-owner"}, headers={"Origin": "http://127.0.0.1:5173"}
    )
    endpoint = f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access"
    assert client.post(endpoint, json={}).status_code == 403 and not requested
    assert (
        client.post(
            endpoint,
            json={},
            headers={"Origin": "https://foreign.example", "X-CSRF-Token": login.json()["csrf_token"]},
        ).status_code
        == 403
        and not requested
    )
    response = client.post(
        endpoint,
        json={},
        headers={"Origin": "http://127.0.0.1:5173", "X-CSRF-Token": login.json()["csrf_token"]},
    )
    assert response.status_code == 200, response.text


def test_repeated_finalization_returns_the_original_immutable_access_receipt(tmp_path):
    client, run, repair, _, requested, *_ = captured_repair(tmp_path)
    url = f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access"
    first = client.post(url, json={}, headers={"Authorization": "Bearer unit-owner"})
    reads = list(requested)
    second = client.post(url, json={}, headers={"Authorization": "Bearer unit-owner"})
    assert first.status_code == 200 and second.json() == first.json()
    assert requested == reads


def test_a_failed_private_receipt_write_cannot_persist_public_success(tmp_path):
    client, run, repair, _, _, settings, *_ = captured_repair(tmp_path)
    transport = client.app.state.service.checkpoint_transport
    reads = 0

    def public_http_with_evidence_failure(request):
        nonlocal reads
        response = transport.handle_request(request)
        reads += 1
        if reads == 4:
            # External filesystem failure after successful offline public reads.
            settings.checkpoint_evidence_root.rename(tmp_path / "retained-evidence")
            settings.checkpoint_evidence_root.write_text("offline filesystem failure fixture")
        return response

    client.app.state.service.checkpoint_transport = httpx.MockTransport(public_http_with_evidence_failure)
    response = client.post(
        f"/runs/{run['id']}/repairs/{repair['id']}/checkpoint-access",
        json={},
        headers={"Authorization": "Bearer unit-owner"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "checkpoint_evidence_unavailable"
    status = client.get(f"/runs/{run['id']}", headers={"Authorization": "Bearer unit-owner"}).json()
    assert status["repair"]["checkpoint_url"] is None and "checkpoint_access" not in status["repair"]
