"""Prepare an audited, disclosed privacy export. Never push or call a provider."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from dotenv import dotenv_values, load_dotenv

from tryfolk.checkpoint_access import load_capture_binding, require
from tryfolk.checkpoint_privacy_export import (
    create_privacy_export,
    git_reader,
    ACCOUNT_FIELDS,
    IDENTITY_FIELDS,
)
from tryfolk.config import Settings


def main():
    root = Path.cwd()
    repair_id = sys.argv[1]
    require(re.fullmatch(r"[a-f0-9]{32}", repair_id))
    load_dotenv(root / "runtime/demo.env")
    private = json.loads((root / "runtime/local-secrets.json").read_text())
    settings = replace(Settings.from_env(), owner_token=private["owner"])
    with sqlite3.connect(settings.db_path) as db:
        repair = json.loads(db.execute("SELECT payload FROM repairs WHERE id=?", (repair_id,)).fetchone()[0])
        run = json.loads(db.execute("SELECT payload FROM runs WHERE id=?", (repair["run_id"],)).fetchone()[0])
    require(
        run["provider"] == repair["provider"] == "dodo" and run["phase"] == repair["status"] == "completed"
    )
    original, original_digest, repository = load_capture_binding(settings, run, repair, original_only=True)
    workspace = root / "runtime/repairs" / repair_id / "source"
    git = git_reader(workspace)
    original_ref = git("rev-parse", original["checkpoint_ref"]).strip()
    require(original_ref.decode() == original["checkpoint_object"])
    original_file = settings.checkpoint_evidence_root / (repair_id + "-checkpoint-access-binding.json")
    original_bytes = original_file.read_bytes()
    export_path = settings.checkpoint_evidence_root / (repair_id + "-checkpoint-privacy-export.json")
    if not export_path.exists():
        create_privacy_export(
            workspace,
            settings.checkpoint_evidence_root / repair_id,
            original,
            original_digest,
            settings.owner_token,
        )
    binding, binding_digest, _ = load_capture_binding(settings, run, repair)
    require(
        "privacy_export" in binding
        and original_file.read_bytes() == original_bytes
        and git("rev-parse", original["checkpoint_ref"]).strip() == original_ref
    )
    original_rows = [
        json.loads(x) for x in git("show", original["checkpoint_object"] + ":0/full.jsonl").splitlines()
    ]
    account_values = set()
    plan_values = set()
    for row in original_rows:
        payload = row.get("payload", {})
        if row.get("type") == "session_meta":
            account_values.update(
                payload[k].encode() for k in IDENTITY_FIELDS if isinstance(payload.get(k), str)
            )
        if row.get("type") == "event_msg" and payload.get("type") == "token_count":
            limits = payload.get("rate_limits") or {}
            balance = (limits.get("credits") or {}).get("balance")
            if isinstance(balance, str):
                account_values.add(balance.encode())
            if isinstance(limits.get("plan_type"), str):
                plan_values.add(json.dumps(limits["plan_type"]).encode())
    secrets = {v.encode() for v in private.values() if isinstance(v, str) and len(v) >= 12}
    for file in (root / "runtime/demo.env", root / "runtime/neatlogs.env", root / "dodo-target/.env.local"):
        if file.is_file():
            secrets.update(
                v.encode()
                for k, v in dotenv_values(file).items()
                if re.search(r"TOKEN|KEY|SECRET|PASSWORD", k) and v and len(v) >= 12
            )
    patterns = [
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        rb"gh[pousr]_[A-Za-z0-9]{25,}",
        rb"github_pat_[A-Za-z0-9_]{25,}",
        rb"sk-[A-Za-z0-9_-]{24,}",
        rb"AKIA[A-Z0-9]{16}",
    ]
    reviewed = []
    for line in (
        git("rev-list", "--objects", binding["source_commit"], binding["checkpoint_object"])
        .decode()
        .splitlines()
    ):
        oid, _, path = line.partition(" ")
        if git("cat-file", "-t", oid).strip() != b"blob":
            continue
        blob = git("cat-file", "blob", oid)
        require(
            not any(v in blob for v in secrets | account_values | plan_values),
            "checkpoint_export_private_value_remaining",
        )
        require(not any(re.search(p, blob) for p in patterns), "checkpoint_export_secret_pattern_remaining")
        reviewed.append(
            {"object": oid, "path": path, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        )
    for row in [
        json.loads(x) for x in git("show", binding["checkpoint_object"] + ":0/full.jsonl").splitlines()
    ]:
        payload = row.get("payload", {})
        if row.get("type") == "session_meta":
            require(not set(IDENTITY_FIELDS) & set(payload))
        if row.get("type") == "event_msg" and payload.get("type") == "token_count":
            require(not set(ACCOUNT_FIELDS) & set(payload.get("rate_limits") or {}))
    publication = settings.checkpoint_evidence_root / (repair_id + "-public.git")
    source_ref = "refs/heads/tryfolk-dodo-repair-" + repair_id
    expected_refs = {
        source_ref: binding["source_commit"],
        binding["checkpoint_ref"]: binding["checkpoint_object"],
    }
    if not publication.exists():
        subprocess.run(["git", "init", "--bare", str(publication)], check=True, capture_output=True)
        packed = git(
            "pack-objects",
            "--stdout",
            "--revs",
            data=(binding["source_commit"] + "\n" + binding["checkpoint_object"] + "\n").encode(),
        )
        subprocess.run(
            ["git", "-C", str(publication), "unpack-objects"], input=packed, check=True, capture_output=True
        )
        for ref, oid in expected_refs.items():
            subprocess.run(
                ["git", "-C", str(publication), "update-ref", ref, oid], check=True, capture_output=True
            )
    require(not publication.is_symlink())
    refs = (
        subprocess.run(
            ["git", "-C", str(publication), "for-each-ref", "--format=%(refname) %(objectname)"],
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .splitlines()
    )
    require(
        dict(line.split() for line in refs) == expected_refs, "checkpoint_export_publication_refs_changed"
    )
    require(
        subprocess.run(
            ["git", "-C", str(publication), "cat-file", "-e", original["checkpoint_object"]],
            capture_output=True,
        ).returncode
        != 0,
        "checkpoint_export_original_object_in_publication",
    )
    require(not (publication / "hooks/pre-push").exists())
    audit = {
        "repair_id": repair_id,
        "source_commit": binding["source_commit"],
        "checkpoint_id": binding["checkpoint_id"],
        "session_id": binding["session_id"],
        "original_checkpoint_object": original["checkpoint_object"],
        "exported_checkpoint_object": binding["checkpoint_object"],
        "original_binding_sha256": original_digest,
        "export_binding_sha256": binding_digest,
        "original_capture_preserved": True,
        "original_ref_unchanged": True,
        "derivative_has_no_parent": True,
        "account_private_values_checked": len(account_values),
        "plan_values_checked": len(plan_values),
        "known_private_credentials_checked": len(secrets),
        "private_matches": 0,
        "reviewed_reachable_blobs": reviewed,
        "disclosure": binding["privacy_export"],
        "publication_repository": str(publication),
        "publication_status": "not_published",
        "push_argv": [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(publication),
            "push",
            "https://github.com/" + repository + ".git",
            source_ref + ":" + source_ref,
            binding["checkpoint_ref"] + ":" + binding["checkpoint_ref"],
        ],
    }
    path = settings.checkpoint_evidence_root / (repair_id + "-privacy-export-audit.json")
    path.write_text(json.dumps(audit, indent=2) + "\n")
    path.chmod(0o600)
    print(
        json.dumps(
            {
                k: audit[k]
                for k in (
                    "repair_id",
                    "source_commit",
                    "checkpoint_id",
                    "exported_checkpoint_object",
                    "original_capture_preserved",
                    "original_ref_unchanged",
                    "private_matches",
                    "publication_status",
                    "push_argv",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
