"""Entire command responses are external-I/O fixtures, never live capture evidence."""

import json
import subprocess
import sys

import pytest

from tryfolk.repair import capture_checkpoint

SESSION = "01a12707-8da5-7be0-8d01-4cd523424fec"
CHECKPOINT = "01M4KGFQMZ13P9MD5CMP524510"


def git(workspace, *args):
    return subprocess.run(
        ["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def workspace_with_entire_fixture(tmp_path, monkeypatch):
    workspace = tmp_path / "source"
    workspace.mkdir()
    git(workspace, "init", "-b", "repair")
    (workspace / "fulfillment.py").write_text(
        'def should_grant(payment):\n    return payment["status"] == "succeeded"\n'
    )
    git(workspace, "add", ".")
    git(workspace, "-c", "user.name=Test", "-c", "user.email=test@tryfolk.invalid", "commit", "-m", "Repair")
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    script = bin_path / "entire"
    script.write_text(f"""#!{sys.executable}
import json,os,sys,time
from pathlib import Path
SESSION={SESSION!r}
CHECKPOINT={CHECKPOINT!r}
state=Path(".git/fixture-captured")
args=sys.argv[1:]
if args[:2] == ["session","attach"]:
    assert os.environ["CI"] == "1"
    assert "--force" not in args
    try:
        os.open("/dev/tty",os.O_RDWR)
    except OSError:
        pass
    else:
        raise RuntimeError("Still inherited controlling terminal")
    state.write_text("captured")
    import subprocess
    base = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
    state_dir = Path(".git/entire-sessions")
    state_dir.mkdir(exist_ok=True)
    (state_dir / (SESSION + ".json")).write_text(json.dumps({{"session_id":SESSION,"last_checkpoint_id":CHECKPOINT,"agent_type":"Codex","base_commit":base}}))
    print("Created checkpoint " + CHECKPOINT,flush=True)
    time.sleep(5)
elif args[:2] == ["session","info"]:
    if not state.exists(): sys.exit(1)
    print(json.dumps({{"session_id":SESSION,"last_checkpoint_id":CHECKPOINT}}))
elif "--json" in args:
    print(json.dumps({{"checkpoint_id":CHECKPOINT,"sessions":[{{"index":0,"session_id":SESSION,"agent":"Codex"}}]}}))
elif "--transcript" in args:
    print(json.dumps({{"type":"session_meta","payload":{{"id":SESSION,"cwd":os.getcwd()}}}}))
else:
    sys.exit(1)
""")
    script.chmod(0o700)
    import os

    monkeypatch.setenv("PATH", str(bin_path) + os.pathsep + os.environ["PATH"])
    return workspace


@pytest.mark.asyncio
async def test_successful_checkpoint_is_reconciled_after_command_timeout_without_second_capture(
    tmp_path, monkeypatch
):
    workspace = workspace_with_entire_fixture(tmp_path, monkeypatch)
    original = git(workspace, "rev-parse", "HEAD")
    head, checkpoint = await capture_checkpoint(workspace, SESSION, tmp_path / "repair-proof", timeout=1)
    assert checkpoint == CHECKPOINT and head != original
    assert "Entire-Checkpoint: " + CHECKPOINT in git(workspace, "log", "-1", "--format=%B")
    proof = json.loads((tmp_path / "repair-proof-checkpoint-association.json").read_text())
    assert proof["session_id"] == SESSION and proof["capture_timed_out"] is True
    assert "Created checkpoint " + CHECKPOINT in (tmp_path / "repair-proof-entire-attach.txt").read_text()
    assert proof["registered_commit"] == head and proof["transcript_sha256"]


@pytest.mark.asyncio
async def test_checkpoint_for_a_different_source_commit_is_rejected_before_linking(tmp_path, monkeypatch):
    workspace = workspace_with_entire_fixture(tmp_path, monkeypatch)
    base = git(workspace, "rev-parse", "HEAD")
    (workspace / "fulfillment.py").write_text("def should_grant(payment):\n    return False\n")
    git(workspace, "add", ".")
    git(
        workspace,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@tryfolk.invalid",
        "commit",
        "-m",
        "Different source",
    )
    head = git(workspace, "rev-parse", "HEAD")
    state_dir = workspace / ".git/entire-sessions"
    state_dir.mkdir()
    (state_dir / (SESSION + ".json")).write_text(
        json.dumps(
            {
                "session_id": SESSION,
                "last_checkpoint_id": CHECKPOINT,
                "agent_type": "Codex",
                "base_commit": base,
            }
        )
    )
    (workspace / ".git/fixture-captured").write_text("captured")
    with pytest.raises(RuntimeError, match="source commit"):
        await capture_checkpoint(workspace, SESSION, tmp_path / "proof", timeout=1, reconcile_only=True)
    assert git(workspace, "rev-parse", "HEAD") == head
    assert "Entire-Checkpoint:" not in git(workspace, "log", "-1", "--format=%B")
