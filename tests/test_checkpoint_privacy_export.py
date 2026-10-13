"""Offline privacy-derivative boundaries; no actual public capture claims."""

import hashlib
import hmac
import json
import shutil
import subprocess

import httpx
import pytest

from test_checkpoint_access import captured_repair, SESSION
from tryfolk.checkpoint_access import (
    CheckpointAccessError,
    encoded,
    load_capture_binding,
    verify_public_access,
)
from tryfolk.checkpoint_privacy_export import SCHEMA, create_privacy_export, privacy_transcript


def prepared(tmp_path):
    client, run, _, _, _, settings, _, _ = captured_repair(tmp_path, privacy_records=True)
    service = client.app.state.service
    with service.store.transaction() as db:
        repair = service.store.load(
            db,
            "repairs",
            run["repair"]["id"] if run.get("repair") else db.execute("SELECT id FROM repairs").fetchone()[0],
        )
    original, original_digest, repo = load_capture_binding(settings, run, repair, original_only=True)
    workspace = tmp_path / "repairs" / repair["id"] / "source"
    workspace.parent.mkdir(parents=True)
    shutil.move(tmp_path / "source", workspace)
    original_path = settings.checkpoint_evidence_root / (repair["id"] + "-checkpoint-access-binding.json")
    immutable = original_path.read_bytes()
    binding, receipt = create_privacy_export(
        workspace,
        settings.checkpoint_evidence_root / repair["id"],
        original,
        original_digest,
        settings.owner_token,
    )
    assert original_path.read_bytes() == immutable
    return settings, run, repair, workspace, original, binding, receipt, repo


def git(workspace, *args, data=None):
    return subprocess.run(
        ["git", "-C", str(workspace), *args], input=data, check=True, capture_output=True
    ).stdout


def resign(settings, repair, edit):
    path = settings.checkpoint_evidence_root / (repair["id"] + "-checkpoint-privacy-export.json")
    envelope = json.loads(path.read_bytes())
    edit(envelope["payload"])
    envelope["signature"] = hmac.new(
        settings.owner_token.encode(), SCHEMA.encode() + b"\0" + encoded(envelope["payload"]), hashlib.sha256
    ).hexdigest()
    path.chmod(0o600)
    path.write_bytes(encoded(envelope))


def test_export_preserves_actual_records_and_original_but_removes_account_fields(tmp_path):
    settings, run, repair, workspace, original, binding, receipt, repo = prepared(tmp_path)
    actual, _, _ = load_capture_binding(settings, run, repair)
    assert actual["checkpoint_object"] == binding["checkpoint_object"] != original["checkpoint_object"]
    assert actual["privacy_export"] == receipt
    assert receipt["original_bytes_unchanged"] is False
    assert receipt["original_private_capture_preserved"] is True
    assert git(
        workspace, "rev-list", "--parents", "-n", "1", binding["checkpoint_object"]
    ).decode().split() == [binding["checkpoint_object"]]
    raw = git(workspace, "show", binding["checkpoint_object"] + ":0/full.jsonl")
    assert b"offline-private" not in raw
    assert b"actual fixture repair request" in raw and b"actual fixture result: test failed" in raw
    assert b'"tokens":123' in raw and b'"limit_id":"codex"' in raw
    assert (
        git(workspace, "show", original["checkpoint_object"] + ":0/full.jsonl").count(b"offline-private") == 4
    )
    assert git(workspace, "rev-parse", "HEAD").decode().strip() == original["source_commit"]
    requests = []

    def read(request):
        assert "authorization" not in request.headers and "cookie" not in request.headers
        requests.append(request)
        spec = request.url.path.removeprefix("/" + repo + "/").split("/", 1)
        return httpx.Response(200, content=git(workspace, "show", spec[0] + ":" + spec[1]))

    result = verify_public_access(actual, "f" * 64, repo, transport=httpx.MockTransport(read))
    assert len(requests) == 5 and result["privacy_export"] == receipt
    assert result["privacy_export_url"].endswith("/privacy-export.json")


@pytest.mark.parametrize(
    "field,value", [("source_commit", "1" * 40), ("session_id", "wrong"), ("checkpoint_id", "wrong")]
)
def test_even_resigned_export_cannot_change_capture_identity(tmp_path, field, value):
    settings, run, repair, *_ = prepared(tmp_path)
    resign(settings, repair, lambda p: p["binding"].update({field: value}))
    with pytest.raises(CheckpointAccessError):
        load_capture_binding(settings, run, repair)


def test_even_resigned_parented_export_cannot_publish_private_capture_ancestry(tmp_path):
    settings, run, repair, workspace, original, binding, *_ = prepared(tmp_path)
    tree = git(workspace, "rev-parse", binding["checkpoint_object"] + "^{tree}").decode().strip()
    parented = (
        git(
            workspace,
            "-c",
            "user.name=Offline",
            "-c",
            "user.email=offline@tryfolk.invalid",
            "commit-tree",
            tree,
            "-p",
            original["checkpoint_object"],
            "-m",
            "Invalid private ancestor",
        )
        .decode()
        .strip()
    )
    resign(settings, repair, lambda p: p["binding"].update(checkpoint_object=parented))
    with pytest.raises(CheckpointAccessError):
        load_capture_binding(settings, run, repair)


def test_even_resigned_export_cannot_modify_real_tool_result(tmp_path):
    settings, run, repair, workspace, _, binding, *_ = prepared(tmp_path)
    raw = git(workspace, "show", binding["checkpoint_object"] + ":0/full.jsonl").replace(
        b"test failed", b"test passed"
    )
    blob = git(workspace, "hash-object", "-w", "--stdin", data=raw).decode().strip()
    sub = git(workspace, "ls-tree", binding["checkpoint_object"] + ":0").decode()
    sub = (
        "\n".join(
            ("100644 blob " + blob + "\tfull.jsonl") if x.endswith("\tfull.jsonl") else x
            for x in sub.splitlines()
        )
        + "\n"
    )
    sub_tree = git(workspace, "mktree", data=sub.encode()).decode().strip()
    top = git(workspace, "ls-tree", binding["checkpoint_object"]).decode()
    top = (
        "\n".join(("040000 tree " + sub_tree + "\t0") if x.endswith("\t0") else x for x in top.splitlines())
        + "\n"
    )
    tree = git(workspace, "mktree", data=top.encode()).decode().strip()
    obj = (
        git(
            workspace,
            "-c",
            "user.name=Offline",
            "-c",
            "user.email=offline@tryfolk.invalid",
            "commit-tree",
            tree,
            "-m",
            "Invalid outcome edit",
        )
        .decode()
        .strip()
    )
    resign(settings, repair, lambda p: p["binding"].update(checkpoint_object=obj))
    with pytest.raises(CheckpointAccessError):
        load_capture_binding(settings, run, repair)


def test_fields_with_same_names_outside_exact_metadata_shapes_are_untouched():
    raw = (
        encoded(
            {
                "type": "response_item",
                "payload": {
                    "creator_user_id": "keep",
                    "rate_limits": {"credits": "keep"},
                    "content": "actual outcome",
                },
            }
        )
        + b"\n"
    )
    assert privacy_transcript(raw, SESSION) == (
        raw,
        {"creator_fields_deleted": 0, "rate_limit_fields_deleted": 0},
    )
