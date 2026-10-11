"""Owned local source repair, real Codex transcript, Entire checkpoint, pinned preview."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import signal
from .codex_bridge import clean_env, MODEL
from .process import ROOT, SDKProcess
from .worker import Cancelled


def checkpoint_commit(workspace, checkpoint_id):
    """Link a captured checkpoint before publishing any immutable preview revision."""
    if not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", checkpoint_id):
        raise ValueError("Invalid captured Entire checkpoint ID.")
    message = subprocess.run(
        ["git", "-C", str(workspace), "log", "-1", "--format=%B"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    existing = re.findall(r"^Entire-Checkpoint:\s*(\S+)$", message, re.MULTILINE)
    if existing and existing != [checkpoint_id]:
        raise RuntimeError("Owned repair commit already links a different checkpoint.")
    if existing:
        return subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "-c",
            "user.name=Tryfolk Local",
            "-c",
            "user.email=local@tryfolk.invalid",
            "commit",
            "--amend",
            "--no-edit",
            "--trailer",
            "Entire-Checkpoint: " + checkpoint_id,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


async def entire_command(workspace, args, *, timeout=30, cancelled=lambda: False):
    """Own one detached process group; retain partial output after timeout/cancellation."""
    process = await asyncio.create_subprocess_exec(
        "entire",
        *args,
        cwd=workspace,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=clean_env() | {"CI": "1"},
        start_new_session=True,
    )
    task = asyncio.create_task(process.communicate())
    deadline = asyncio.get_running_loop().time() + timeout
    timed_out = False
    was_cancelled = False
    while not task.done():
        was_cancelled = cancelled()
        timed_out = asyncio.get_running_loop().time() > deadline
        if was_cancelled or timed_out:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(asyncio.shield(task), 2)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await task
            break
        await asyncio.sleep(0.05)
    stdout, stderr = await task
    if was_cancelled:
        raise Cancelled()
    return stdout, stderr, process.returncode, timed_out


async def capture_checkpoint(
    workspace, session_id, evidence_prefix, *, timeout=30, cancelled=lambda: False, reconcile_only=False
):
    """Verify a real session checkpoint before linking its immutable source commit.

    Entire 0.11.4 probes /dev/tty independently of stdin. A detached session and
    its supported CI sentinel prevent prompts without --force. A timeout is
    ambiguous: read the exact stored session, never repeat attach automatically.
    """

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    head = git("rev-parse", "HEAD")
    tree = git("rev-parse", "HEAD^{tree}")
    if git("status", "--porcelain"):
        raise RuntimeError("Checkpoint capture requires the exact clean owned repair commit.")

    async def read(args):
        stdout, stderr, code, expired = await entire_command(
            workspace, args, timeout=timeout, cancelled=cancelled
        )
        if code or expired:
            raise RuntimeError("Entire readback unavailable; capture is not repeated.")
        return stdout

    state_bytes, _, code, expired = await entire_command(
        workspace, ["session", "info", session_id, "--json"], timeout=timeout, cancelled=cancelled
    )
    state = json.loads(state_bytes) if code == 0 and not expired else {}
    checkpoint = state.get("last_checkpoint_id")
    timed_out = False
    if not checkpoint:
        if reconcile_only or expired:
            raise RuntimeError("No verified checkpoint for this saved session; no attach or repair repeated.")
        stdout, stderr, code, timed_out = await entire_command(
            workspace,
            ["session", "attach", session_id, "--agent", "codex"],
            timeout=timeout,
            cancelled=cancelled,
        )
        evidence_prefix.with_name(evidence_prefix.name + "-entire-attach.txt").write_bytes(stdout + stderr)
        # Read durable state even when capture returned an error or timed out.
        state_bytes = await read(["session", "info", session_id, "--json"])
        state = json.loads(state_bytes)
        checkpoint = state.get("last_checkpoint_id")
    if (
        state.get("session_id") != session_id
        or not isinstance(checkpoint, str)
        or not re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", checkpoint)
    ):
        raise RuntimeError("Captured checkpoint does not identify the exact Codex repair session.")
    # This is the pinned 0.11.4 local state that attach persists before prompting.
    # It binds this exact session to the source commit, including legacy timeouts
    # that occurred before our outer worker could persist capture-state.json.
    local_state_path = workspace / ".git/entire-sessions" / (session_id + ".json")
    local_state = json.loads(local_state_path.read_text())
    base = local_state.get("base_commit", "")
    if (
        local_state.get("session_id") != session_id
        or local_state.get("last_checkpoint_id") != checkpoint
        or local_state.get("agent_type") != "Codex"
        or not re.fullmatch(r"[0-9a-f]{40}", base)
        or git("rev-parse", base + "^{tree}") != tree
    ):
        raise RuntimeError("Entire capture does not match the saved repair source commit.")
    metadata_bytes = await read(["checkpoint", "explain", checkpoint, "--json"])
    metadata = json.loads(metadata_bytes)
    matching = [s for s in metadata.get("sessions", []) if s.get("session_id") == session_id]
    if (
        metadata.get("checkpoint_id") != checkpoint
        or len(matching) != 1
        or matching[0].get("agent") != "Codex"
    ):
        raise RuntimeError("Entire checkpoint metadata does not match the exact Codex repair session.")
    transcript = await read(
        ["checkpoint", "explain", checkpoint, "--transcript", "--session-index", str(matching[0]["index"])]
    )
    entries = [json.loads(line) for line in transcript.splitlines() if line.startswith(b"{")]
    session_meta = [e.get("payload", {}) for e in entries if e.get("type") == "session_meta"]
    if not any(s.get("id") == session_id and s.get("cwd") == str(workspace.resolve()) for s in session_meta):
        raise RuntimeError("Entire transcript does not identify the exact owned repair workspace/session.")
    if cancelled():
        raise Cancelled()
    if (
        git("rev-parse", "HEAD") != head
        or git("rev-parse", "HEAD^{tree}") != tree
        or git("status", "--porcelain")
    ):
        raise RuntimeError("Owned repair revision changed during Entire capture.")
    registered = checkpoint_commit(workspace, checkpoint)
    explain = await read(["checkpoint", "explain", "--commit", registered, "--json"])
    if json.loads(explain).get("checkpoint_id") != checkpoint or git("rev-parse", "HEAD^{tree}") != tree:
        raise RuntimeError("Registered repair commit does not link the exact captured checkpoint/source.")
    for suffix, data in (("-entire-checkpoint.json", explain), ("-entire-transcript.jsonl", transcript)):
        evidence_prefix.with_name(evidence_prefix.name + suffix).write_bytes(data)
    evidence_prefix.with_name(evidence_prefix.name + "-checkpoint-association.json").write_text(
        json.dumps(
            {
                "capture_commit": head,
                "registered_commit": registered,
                "checkpoint_id": checkpoint,
                "session_id": session_id,
                "capture_timed_out": timed_out,
                "reconciled_existing": bool(reconcile_only),
                "transcript_sha256": hashlib.sha256(transcript).hexdigest(),
                "source_unchanged_by_trailer": True,
            },
            indent=2,
        )
    )
    return registered, checkpoint


async def recover_target(runtime, run, emit):
    """Finish a saved repair using read-only checkpoint reconciliation, without Codex."""
    if run["provider"] == "dodo":
        from .dodo_repair import repair

        return await repair(runtime, run, emit, reconcile_only=True)
    service = runtime.service
    repair = run["repair"]
    if run["provider"] != "local-fixture" or run["pass"] != "before":
        raise RuntimeError("Only an already-saved owned local fixture repair can be reconciled.")
    workspace = ROOT / "runtime/repairs" / repair["id"] / "source"
    if not workspace.is_dir() or not (workspace / ".git").is_dir():
        raise RuntimeError("No saved isolated repair workspace; code repair is not repeated.")
    stdout = (runtime.evidence / f"{repair['id']}-codex-repair.jsonl").read_bytes()
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith(b"{")]
    if not any(e.get("type") == "turn.completed" for e in events):
        raise RuntimeError("Saved Codex repair did not complete; no new repair is started by recovery.")
    with service.store.transaction() as db:
        current = service.store.load(db, "runs", run["id"])
        if current["phase"] == "cancelled":
            raise Cancelled()
        repair = current["repair"]
        repair["status"] = "running"
        current["repair"] = repair
        service.store.save(db, "repairs", repair)
        service.store.save(db, "runs", current)
    await finalize_target(runtime, run, repair, workspace, stdout, emit, reconcile_only=True)


async def repair_target(runtime, run, emit):
    if run["provider"] == "dodo":
        from .dodo_repair import repair

        return await repair(runtime, run, emit)
    service = runtime.service
    with service.store.transaction() as db:
        current = service.store.load(db, "runs", run["id"])
        repair = current["repair"]
        repair["status"] = "running"
        current["repair"] = repair
        service.store.save(db, "repairs", repair)
        service.store.save(db, "runs", current)
        finding = service.store.load(db, "findings", repair["finding_id"])
    if run["provider"] != "local-fixture":
        raise RuntimeError(
            "Dodo target repair requires its owned source/reset/preview grant; local fixture is the current supported repair target."
        )
    await runtime.check_revision(run)
    workspace = ROOT / "runtime/repairs" / repair["id"] / "source"
    workspace.mkdir(parents=True)
    source = ROOT / "owned-target/fixture/fulfillment.py"
    if hashlib.sha256(source.read_bytes()).hexdigest() != run["revision"]:
        raise RuntimeError("Owned source no longer matches baseline revision.")
    shutil.copy2(source, workspace / "fulfillment.py")
    (workspace / "test_fulfillment.py").write_text(
        'from fulfillment import should_grant\n\ndef test_success():\n    assert should_grant({"status":"succeeded"}) is True\n\ndef test_non_success():\n    assert should_grant({"status":"pending"}) is False\n    assert should_grant({"status":"failed"}) is False\n'
    )

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    (workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n.entire/\n")
    git("init", "-b", "tryfolk-repair")
    red = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "test_fulfillment.py"],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    (runtime.evidence / f"{repair['id']}-red.txt").write_text(red.stdout + red.stderr)
    if red.returncode != 1:
        raise RuntimeError("Owned defect regression did not fail as expected before repair.")
    git("add", ".")
    git(
        "-c",
        "user.name=Tryfolk Local",
        "-c",
        "user.email=local@tryfolk.invalid",
        "commit",
        "-m",
        "Pin owned fixture defect and regression assertions",
    )
    (workspace / ".entire").mkdir()
    (workspace / ".entire/settings.local.json").write_text(
        '{"enabled":true,"strategy_options":{"push_sessions":false},"checkpoints":{"primary":{"type":"git-refs"}}}'
    )
    prompt = (
        "Repair only fulfillment.py in this owned local source workspace. This is a deliberately disclosed local-fixture paid-without-access defect. "
        "Payment readback reports succeeded for the same account but application entitlement remains absent. "
        "Use the regression assertions already present: grant only succeeded, deny pending/failed. Do not edit tests. "
        "Do not read outside this workspace, delegate, push, make network calls or create remote repositories. "
        "Implement the minimal correct fix; leave committing to the outer worker.\nSanitized finding:\n"
        + json.dumps(finding["evidence"])
    )
    process = await asyncio.create_subprocess_exec(
        "codex",
        "exec",
        "--ignore-user-config",
        "--disable",
        "hooks",
        "--model",
        MODEL,
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace),
        "--json",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=clean_env(),
        start_new_session=True,
    )
    task = asyncio.create_task(process.communicate(prompt.encode()))
    deadline = asyncio.get_running_loop().time() + 300
    while not task.done():
        if (
            service.get_run(run["id"], service.settings.owner_token)["phase"] == "cancelled"
            or asyncio.get_running_loop().time() > deadline
        ):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(task, 10)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError("Codex repair time allowance exhausted; no retry.")
            raise Cancelled()
        await asyncio.sleep(0.25)
    stdout, stderr = await task
    (runtime.evidence / f"{repair['id']}-codex-repair.jsonl").write_bytes(stdout)
    if process.returncode:
        raise RuntimeError("Codex repair failed; see repair transcript evidence.")
    await finalize_target(runtime, run, repair, workspace, stdout, emit)


async def finalize_target(runtime, run, repair, workspace, stdout, emit, *, reconcile_only=False):
    """Complete an already-authorized Codex repair after validating its actual source."""
    service = runtime.service

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", str(workspace / "test_fulfillment.py")],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    (runtime.evidence / f"{repair['id']}-validation.txt").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Exact repaired source failed its regression assertions.")
    # Verify Codex changed only the allowed owned source.
    changed = git("diff", "--name-only").splitlines()
    resumed_commit = (
        not changed
        and git("log", "-1", "--format=%s") == "Restore fulfillment only for succeeded fixture payments"
        and git("diff", "HEAD~1", "HEAD", "--name-only").splitlines() == ["fulfillment.py"]
    )
    if changed != ["fulfillment.py"] and not resumed_commit:
        raise RuntimeError("Repair modified files outside the authorized source scope.")
    if reconcile_only and not resumed_commit:
        raise RuntimeError("Recovery requires the existing clean committed repair; no code repair repeated.")
    if not resumed_commit:
        git("add", "fulfillment.py")
        git(
            "-c",
            "user.name=Tryfolk Local",
            "-c",
            "user.email=local@tryfolk.invalid",
            "commit",
            "-m",
            "Restore fulfillment only for succeeded fixture payments",
        )
    head = git("rev-parse", "HEAD")
    events = [json.loads(line) for line in stdout.decode().splitlines() if line.startswith("{")]
    session_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    if not session_id:
        raise RuntimeError("Codex repair produced no persistent session ID for Entire capture.")
    state_path = workspace.parent / "capture-state.json"
    state = {
        "run_id": run["id"],
        "repair_id": repair["id"],
        "session_id": session_id,
        "source_commit": head,
        "source_digest": hashlib.sha256((workspace / "fulfillment.py").read_bytes()).hexdigest(),
        "tests_digest": hashlib.sha256((workspace / "test_fulfillment.py").read_bytes()).hexdigest(),
    }
    if state_path.exists():
        saved = json.loads(state_path.read_text())
        if any(
            saved.get(k) != state[k]
            for k in ("run_id", "repair_id", "session_id", "source_digest", "tests_digest")
        ):
            raise RuntimeError("Saved capture identity/source no longer matches this repair.")
        if git("rev-parse", saved["source_commit"] + "^{tree}") != git("rev-parse", "HEAD^{tree}"):
            raise RuntimeError("Saved repair source commit no longer matches the exact source tree.")
    else:
        state_path.write_text(json.dumps(state, indent=2))
    head, checkpoint = await capture_checkpoint(
        workspace,
        session_id,
        runtime.evidence / repair["id"],
        cancelled=lambda: runtime.cancelled(run["id"]),
        reconcile_only=reconcile_only,
    )
    digest = hashlib.sha256((workspace / "fulfillment.py").read_bytes()).hexdigest()
    # The durable supervisor owns all preview Popen handles after this call returns.
    from .previews import PreviewRegistry

    registry = PreviewRegistry(service.store, ROOT / "runtime/repairs")
    preview = registry.request(
        repair["id"], source=workspace / "fulfillment.py", revision=head, source_digest=digest
    )
    preview_url = preview["origin"]
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        if runtime.cancelled(run["id"]):
            raise Cancelled()
        preview = registry.get(repair["id"])
        if preview["status"] == "ready":
            break
        if preview["status"] == "failed":
            raise RuntimeError(preview["error"])
        await asyncio.sleep(0.25)
    else:
        raise RuntimeError(
            "Persistent local supervisor did not make the exact preview healthy. Start scripts/local.py."
        )
    service.register_repair(
        repair["id"],
        head_revision=head,
        preview_url=preview_url,
        checkpoint_id=checkpoint,
        checkpoint_url=None,
        provider=run["provider"],
        source_digest=digest,
    )
    await emit(
        "repair",
        {
            "repair_id": repair["id"],
            "base_revision": run["revision"],
            "head_revision": head,
            "source_digest": digest,
            "preview_url": preview_url,
            "checkpoint_id": checkpoint,
            "checkpoint_url": None,
            "session_id": session_id,
            "validation": "2 regression assertions passed",
            "public_checkpoint_gate": "Checkpoint is local; accessible public link/account grant still required.",
        },
    )


async def replay_known_failure(runtime, run, population, emit):
    service = runtime.service
    with service.store.transaction() as db:
        before = service.store.load(db, "runs", run["parent_run_id"])
        failure = next((v for v in before["outcomes"].values() if v.get("outcome") == "failed"), None)
    if not failure or not failure.get("actions"):
        raise RuntimeError(
            "Known failing browser actions unavailable; adaptive retest is not a substitute for replay."
        )
    fixture = await runtime.provision(run, population[0])
    browser = SDKProcess("stagehand")
    try:
        await browser.request({"op": "goto", "url": run["preview_url"] + fixture["session_url"]})
        observed_text = ""
        for action in failure["actions"]:
            if action.get("method") != "click" or action.get("arguments"):
                raise RuntimeError("Known action replay exceeds bounded tool contract.")
            execution = await browser.request({"op": "act", "action": action})
            if not execution.get("data", {}).get("success"):
                raise RuntimeError("Recorded diagnostic browser action failed.")
            observed_text = execution.get("observed_text", "")
        # Capability already executed by original action sequence; GET entitlement remains read-only.
        observed_lower = observed_text.lower()
        capability = (
            True
            if "purchased capability available" in observed_lower
            else False
            if "purchase required" in observed_lower
            else None
        )
        result = await runtime.verification(run, fixture, capability)
        await emit(
            "known_failure_replay",
            {
                **result,
                "account_id": fixture["account_id"],
                "diagnostic": True,
                "not_in_population_denominator": True,
                "conditions": population[0]["conditions"],
                "actions": failure["actions"],
                "observed_text": observed_text,
            },
        )
        if result["outcome"] != "completed":
            raise RuntimeError("Known failure replay did not pass; full retest stopped honestly.")
    finally:
        await browser.close()
