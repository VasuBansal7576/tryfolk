"""Disclosed privacy derivative of a genuine private capture; never a recapture."""

import hashlib
import hmac
import json
from pathlib import Path
import re
import subprocess

from .checkpoint_access import MAX_BYTES, digest, encoded, private_write, require
from .codex_bridge import clean_env


SCHEMA = "tryfolk.checkpoint-privacy-export.v1"
RECEIPT_PATH = "privacy-export.json"
IDENTITY_FIELDS = ("creator_user_id", "creator_account_id")
ACCOUNT_FIELDS = (
    "primary",
    "secondary",
    "credits",
    "individual_limit",
    "spend_control_reached",
    "plan_type",
    "rate_limit_reached_type",
)
POLICY = "delete_codex_creator_identity_and_rate_limit_account_metadata.v1"


def privacy_transcript(raw, session):
    """Only two exact Codex record shapes may change; preserve other raw lines."""
    output, counts = [], {"creator_fields_deleted": 0, "rate_limit_fields_deleted": 0}
    for line in raw.splitlines(keepends=True):
        item = json.loads(line)
        payload = item.get("payload", {})
        changed = False
        if item.get("type") == "session_meta":
            require(payload.get("id") == session, "checkpoint_privacy_export_invalid")
            for key in IDENTITY_FIELDS:
                if key in payload:
                    del payload[key]
                    counts["creator_fields_deleted"] += 1
                    changed = True
        elif item.get("type") == "event_msg" and payload.get("type") == "token_count":
            limits = payload.get("rate_limits")
            if isinstance(limits, dict):
                for key in ACCOUNT_FIELDS:
                    if key in limits:
                        del limits[key]
                        counts["rate_limit_fields_deleted"] += 1
                        changed = True
        output.append(encoded(item) + (b"\n" if line.endswith(b"\n") else b"") if changed else line)
    return b"".join(output), counts


def git_reader(workspace):
    env = clean_env() | {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"}

    def git(*args, data=None):
        return subprocess.run(
            ["git", "-C", str(workspace), *args],
            input=data,
            env=env,
            check=True,
            capture_output=True,
            timeout=10,
        ).stdout

    return git


def checkpoint_files(git, obj):
    files = {}
    for line in git("ls-tree", "-r", obj).decode().splitlines():
        mode_kind_oid, path = line.split("\t", 1)
        mode, kind, oid = mode_kind_oid.split()
        require(mode == "100644" and kind == "blob", "checkpoint_privacy_export_invalid")
        require(int(git("cat-file", "-s", oid)) <= MAX_BYTES)
        files[path] = git("cat-file", "blob", oid)
    require(
        set(files)
        in (
            {"metadata.json", "0/metadata.json", "0/full.jsonl", "0/transcript.jsonl"},
            {
                "metadata.json",
                "0/metadata.json",
                "0/full.jsonl",
                "0/transcript.jsonl",
                "0/prompt.txt",
                "0/content_hash.txt",
            },
        ),
        "checkpoint_privacy_export_invalid",
    )
    return files


def export_receipt(original, original_digest, raw, counts):
    return {
        "schema": SCHEMA,
        "kind": "privacy_filtered_publication_derivative",
        "policy": POLICY,
        "original_bytes_unchanged": False,
        "original_private_capture_preserved": True,
        "original_private_binding_file": original["repair_id"] + "-checkpoint-access-binding.json",
        "original_binding_sha256": original_digest,
        "original_checkpoint_object": original["checkpoint_object"],
        "original_transcript_sha256": original["resources"][2]["sha256"],
        "exported_transcript_sha256": digest(raw),
        "run_id": original["run_id"],
        "repair_id": original["repair_id"],
        "checkpoint_id": original["checkpoint_id"],
        "session_id": original["session_id"],
        "source_commit": original["source_commit"],
        "source_tree": original["source_tree"],
        "source_digest": original["source_digest"],
        "removed_fields": counts,
        "retained": ["actual_session", "repair_prompt", "actions", "tool_results", "source_commit"],
    }


def expected_export(git, original, original_digest):
    files = checkpoint_files(git, original["checkpoint_object"])
    require(digest(files["0/full.jsonl"]) == original["resources"][2]["sha256"])
    raw, counts = privacy_transcript(files["0/full.jsonl"], original["session_id"])
    require(
        counts["creator_fields_deleted"] or counts["rate_limit_fields_deleted"],
        "checkpoint_privacy_export_unneeded",
    )
    files["0/full.jsonl"] = raw
    if "0/content_hash.txt" in files:
        files["0/content_hash.txt"] = ("sha256:" + digest(raw)).encode()
    receipt = export_receipt(original, original_digest, raw, counts)
    files[RECEIPT_PATH] = encoded(receipt)
    return files, receipt


def create_privacy_export(workspace, evidence_prefix, original, original_digest, owner_token):
    """Authorized coordinator: write new orphan objects/private ref; never push."""
    workspace, evidence_prefix = Path(workspace).resolve(), Path(evidence_prefix)
    require(owner_token and not evidence_prefix.resolve().is_relative_to(workspace))
    git = git_reader(workspace)
    require(
        git("rev-parse", "HEAD").decode().strip() == original["source_commit"]
        and not git("status", "--porcelain")
    )
    files, receipt = expected_export(git, original, original_digest)
    blobs = {p: git("hash-object", "-w", "--stdin", data=b).decode().strip() for p, b in files.items()}

    def tree(prefix):
        entries = []
        names = sorted({p[len(prefix) :].split("/", 1)[0] for p in blobs if p.startswith(prefix)})
        for name in names:
            p = prefix + name
            if p in blobs:
                entries.append(f"100644 blob {blobs[p]}\t{name}\n")
            else:
                entries.append(f"040000 tree {tree(p + '/')}\t{name}\n")
        return git("mktree", data="".join(entries).encode()).decode().strip()

    # No parent: the private full transcript must never be reachable remotely.
    obj = (
        git(
            "-c",
            "user.name=Tryfolk Privacy Export",
            "-c",
            "user.email=privacy-export@tryfolk.invalid",
            "commit-tree",
            tree(""),
            "-m",
            "Disclosed privacy export of genuine Entire checkpoint " + original["checkpoint_id"],
        )
        .decode()
        .strip()
    )
    binding = dict(original)
    binding["checkpoint_object"] = obj
    binding["resources"] = (
        [
            {"object": obj, "path": p, "sha256": digest(files[p]), "bytes": len(files[p])}
            for p in ("metadata.json", "0/metadata.json", "0/full.jsonl")
        ]
        + [original["resources"][3]]
        + [
            {
                "object": obj,
                "path": RECEIPT_PATH,
                "sha256": digest(files[RECEIPT_PATH]),
                "bytes": len(files[RECEIPT_PATH]),
            }
        ]
    )
    payload = {
        "schema": SCHEMA,
        "original_binding_sha256": original_digest,
        "binding": binding,
        "receipt": receipt,
    }
    signature = hmac.new(
        owner_token.encode(), SCHEMA.encode() + b"\0" + encoded(payload), hashlib.sha256
    ).hexdigest()
    private_write(
        evidence_prefix.with_name(evidence_prefix.name + "-checkpoint-privacy-export.json"),
        encoded({"payload": payload, "signature": signature}),
    )
    git("update-ref", "refs/tryfolk/privacy-exports/" + original["repair_id"], obj)
    return binding, receipt


def load_privacy_export(settings, original, original_digest):
    path = settings.checkpoint_evidence_root / (original["repair_id"] + "-checkpoint-privacy-export.json")
    if not path.exists() and not path.is_symlink():
        return None
    try:
        require(not path.is_symlink() and path.stat().st_size <= 100_000)
        envelope = json.loads(path.read_bytes())
        payload = envelope["payload"]
        signature = hmac.new(
            settings.owner_token.encode(), SCHEMA.encode() + b"\0" + encoded(payload), hashlib.sha256
        ).hexdigest()
        require(hmac.compare_digest(signature, envelope["signature"]))
        require(payload["schema"] == SCHEMA and payload["original_binding_sha256"] == original_digest)
        binding = payload["binding"]
        require(set(binding) == set(original))
        require(
            all(
                binding.get(k) == v
                for k, v in original.items()
                if k not in ("checkpoint_object", "resources")
            )
        )
        obj = binding["checkpoint_object"]
        require(re.fullmatch(r"[a-f0-9]{40}", obj) and obj != original["checkpoint_object"])
        # The source workspace remains trusted coordinator-owned, never model input.
        workspace = settings.checkpoint_evidence_root.parent / "repairs" / original["repair_id"] / "source"
        git = git_reader(workspace)
        require(git("rev-list", "--parents", "-n", "1", obj).decode().split() == [obj])
        expected, receipt = expected_export(git, original, original_digest)
        actual = {}
        for line in git("ls-tree", "-r", obj).decode().splitlines():
            spec, p = line.split("\t", 1)
            mode, kind, oid = spec.split()
            require(mode == "100644" and kind == "blob" and int(git("cat-file", "-s", oid)) <= MAX_BYTES)
            actual[p] = git("cat-file", "blob", oid)
        require(actual == expected and payload["receipt"] == receipt)
        resources = (
            [
                {"object": obj, "path": p, "sha256": digest(expected[p]), "bytes": len(expected[p])}
                for p in ("metadata.json", "0/metadata.json", "0/full.jsonl")
            ]
            + [original["resources"][3]]
            + [
                {
                    "object": obj,
                    "path": RECEIPT_PATH,
                    "sha256": digest(expected[RECEIPT_PATH]),
                    "bytes": len(expected[RECEIPT_PATH]),
                }
            ]
        )
        require(binding["resources"] == resources)
        result = dict(binding)
        result["privacy_export"] = receipt
        return result, digest(encoded(envelope))
    except Exception as error:
        from .checkpoint_access import CheckpointAccessError

        if isinstance(error, CheckpointAccessError):
            raise
        raise CheckpointAccessError("checkpoint_privacy_export_invalid") from None
