"""Verify public bytes for one server-bound Codex capture; never publish or recapture."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import time

import httpx

from .codex_bridge import clean_env


MAX_BYTES = 4_000_000
SCHEMA = "tryfolk.checkpoint-publication-binding.v1"
SEAM = "lib/fulfillment-policy.ts"


class CheckpointAccessError(ValueError):
    pass


def require(condition, code="checkpoint_capture_mismatch"):
    if not condition:
        raise CheckpointAccessError(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def repository_slug(value):
    require(
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", value)
        and value.split("/")[1] not in (".", ".."),
        "checkpoint_repository_unapproved",
    )
    return value


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.parent.is_symlink(), "checkpoint_evidence_unavailable")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        require(not path.is_symlink() and path.read_bytes() == data, "checkpoint_evidence_changed")
        return
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    path.chmod(0o400)


def seal_capture_binding(
    workspace, evidence_prefix, *, run_id, repair_id, source_digest, owner_token, repository=None
):
    """Trusted coordinator only, after its genuine turn/source/capture validations.

    The signature stays outside the model-visible workspace and binds the actual
    Git/session/association bytes. This does not approve privacy or publication.
    """
    require(
        owner_token and re.fullmatch(r"[a-f0-9]{32}", run_id) and re.fullmatch(r"[a-f0-9]{32}", repair_id)
    )
    if repository is not None:
        repository_slug(repository)
    workspace, evidence_prefix = Path(workspace).resolve(), Path(evidence_prefix)
    require((workspace / ".git").is_dir() and not (workspace / ".git").is_symlink())
    require(not evidence_prefix.resolve().is_relative_to(workspace))
    env = clean_env() | {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(workspace), *args], env=env, check=True, capture_output=True, timeout=10
        ).stdout

    def blob(obj, path):
        spec = obj + ":" + path
        require(int(git("cat-file", "-s", spec)) <= MAX_BYTES)
        return git("show", spec)

    association = json.loads(
        evidence_prefix.with_name(evidence_prefix.name + "-checkpoint-association.json").read_bytes()
    )
    source = association["registered_commit"]
    checkpoint, session = association["checkpoint_id"], association["session_id"]
    require(re.fullmatch(r"[a-f0-9]{40}", source) and re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", checkpoint))
    require(git("rev-parse", "HEAD").decode().strip() == source and not git("status", "--porcelain"))
    tree = git("rev-parse", source + "^{tree}").decode().strip()
    require(git("rev-parse", association["capture_commit"] + "^{tree}").decode().strip() == tree)
    require(
        re.findall(r"^Entire-Checkpoint:\s*(\S+)$", git("log", "-1", "--format=%B").decode(), re.M)
        == [checkpoint]
    )
    state = json.loads((workspace / ".git/entire-sessions" / (session + ".json")).read_bytes())
    require(
        state.get("session_id") == session
        and state.get("last_checkpoint_id") == checkpoint
        and state.get("agent_type") == "Codex"
        and git("rev-parse", state["base_commit"] + "^{tree}").decode().strip() == tree
    )
    refs = [
        line.split()
        for line in git("for-each-ref", "--format=%(refname) %(objectname)", "refs/entire/checkpoints")
        .decode()
        .splitlines()
        if line.split()[0].endswith("/" + checkpoint)
    ]
    require(len(refs) == 1)
    ref, obj = refs[0]
    metadata, session_metadata = blob(obj, "metadata.json"), blob(obj, "0/metadata.json")
    meta, smeta = json.loads(metadata), json.loads(session_metadata)
    require(
        meta.get("checkpoint_id") == checkpoint
        and len(meta.get("sessions", [])) == 1
        and meta["sessions"][0].get("metadata") in ("0/metadata.json", "/0/metadata.json")
        and not meta.get("tasks")
        and smeta.get("checkpoint_id") == checkpoint
        and smeta.get("session_id") == session
        and smeta.get("agent") == "Codex"
    )
    # Entire's compact pointer may differ from its actual --transcript output.
    raw_path, raw = None, None
    for path in ("0/full.jsonl", "0/transcript.jsonl"):
        if git("ls-tree", "--name-only", obj, "--", path).strip():
            candidate = blob(obj, path)
            if digest(candidate) == association["transcript_sha256"]:
                raw_path, raw = path, candidate
                break
    require(raw is not None)
    entries = [json.loads(line) for line in raw.splitlines() if line.startswith(b"{")]
    require(
        any(
            e.get("type") == "session_meta"
            and e.get("payload", {}).get("id") == session
            and e.get("payload", {}).get("cwd") == str(workspace)
            for e in entries
        )
    )
    resources = [
        {"object": oid, "path": path, "sha256": digest(data), "bytes": len(data)}
        for oid, path, data in (
            (obj, "metadata.json", metadata),
            (obj, "0/metadata.json", session_metadata),
            (obj, raw_path, raw),
            (source, SEAM, blob(source, SEAM)),
        )
    ]
    payload = {
        "schema": SCHEMA,
        "run_id": run_id,
        "repair_id": repair_id,
        "repository": repository,
        "checkpoint_id": checkpoint,
        "checkpoint_ref": ref,
        "checkpoint_object": obj,
        "session_id": session,
        "source_commit": source,
        "source_tree": tree,
        "source_digest": source_digest,
        "resources": resources,
    }
    signature = hmac.new(
        owner_token.encode(), SCHEMA.encode() + b"\0" + encoded(payload), hashlib.sha256
    ).hexdigest()
    private_write(
        evidence_prefix.with_name(evidence_prefix.name + "-checkpoint-access-binding.json"),
        encoded({"payload": payload, "signature": signature}),
    )


def load_capture_binding(settings, run, repair):
    try:
        require(re.fullmatch(r"[a-f0-9]{32}", repair["id"]))
        path = settings.checkpoint_evidence_root / (repair["id"] + "-checkpoint-access-binding.json")
        require(not path.is_symlink() and path.stat().st_size <= 100_000)
        envelope = json.loads(path.read_bytes())
        payload = envelope["payload"]
        expected = hmac.new(
            settings.owner_token.encode(), SCHEMA.encode() + b"\0" + encoded(payload), hashlib.sha256
        ).hexdigest()
        require(hmac.compare_digest(expected, envelope["signature"]))
        require(
            payload["schema"] == SCHEMA
            and payload["run_id"] == run["id"]
            and payload["repair_id"] == repair["id"]
            and payload["checkpoint_id"] == repair["checkpoint_id"]
            and payload["source_commit"] == repair["head_revision"]
            and payload["source_digest"] == repair["source_digest"]
        )
        require(
            all(
                re.fullmatch(r"[a-f0-9]{40}", payload[key])
                for key in ("checkpoint_object", "source_commit", "source_tree")
            )
        )
        require(
            re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", payload["checkpoint_id"])
            and re.fullmatch(
                r"refs/entire/checkpoints/[A-Za-z0-9]{1,8}/" + payload["checkpoint_id"],
                payload["checkpoint_ref"],
            )
        )
        resources = payload["resources"]
        require(len(resources) == 4 and resources[2]["path"] in ("0/full.jsonl", "0/transcript.jsonl"))
        for resource, path, obj in zip(
            resources,
            ("metadata.json", "0/metadata.json", resources[2]["path"], SEAM),
            (payload["checkpoint_object"],) * 3 + (payload["source_commit"],),
        ):
            require(
                resource["path"] == path
                and resource["object"] == obj
                and type(resource["bytes"]) is int
                and 0 <= resource["bytes"] <= MAX_BYTES
                and re.fullmatch(r"[a-f0-9]{64}", resource["sha256"])
            )
        repository = repository_slug(settings.checkpoint_repository)
        require(payload["repository"] in (None, repository), "checkpoint_repository_unapproved")
        return payload, digest(encoded(envelope)), repository
    except CheckpointAccessError:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        raise CheckpointAccessError("checkpoint_capture_unavailable") from None


def verify_public_access(binding, binding_digest, repository, *, transport=None):
    urls = []
    deadline = time.monotonic() + 25
    try:
        with httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=5,
            headers={"Accept-Encoding": "identity"},
        ) as client:
            for resource in binding["resources"]:
                client.cookies.clear()
                url = (
                    f"https://raw.githubusercontent.com/{repository}/{resource['object']}/{resource['path']}"
                )
                with client.stream("GET", url) as response:
                    require(
                        response.status_code == 200
                        and str(response.url) == url
                        and not response.headers.get("location")
                        and response.headers.get("content-encoding", "identity") == "identity",
                        "checkpoint_public_access_unverified",
                    )
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        require(
                            time.monotonic() < deadline and len(content) + len(chunk) <= MAX_BYTES,
                            "checkpoint_public_access_unverified",
                        )
                        content.extend(chunk)
                    require(
                        len(content) == resource["bytes"] and digest(content) == resource["sha256"],
                        "checkpoint_public_bytes_mismatch",
                    )
                urls.append(url)
    except httpx.HTTPError:
        raise CheckpointAccessError("checkpoint_public_access_unverified") from None
    return {
        "kind": "github_git_checkpoint",
        "status": "anonymous_verified",
        "repository": repository,
        "checkpoint_id": binding["checkpoint_id"],
        "checkpoint_object": binding["checkpoint_object"],
        "checkpoint_ref": binding["checkpoint_ref"],
        "source_commit": binding["source_commit"],
        "url": urls[0],
        "transcript_url": urls[2],
        "source_url": urls[3],
        "binding_sha256": binding_digest,
        "resource_digests": binding["resources"],
        "hosted_entire_url": None,
        "hosted_entire_status": "unverified",
    }
