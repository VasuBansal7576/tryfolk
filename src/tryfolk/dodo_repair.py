"""Real Codex/Entire repair of the sole approved owned Dodo source seam."""

import asyncio
import json
import os
import signal
from .codex_bridge import clean_env, MODEL
from .process import ROOT
from .source_workspace import OwnedSource, SOURCE_FILE, entire_installer_env, git, source_fingerprint, write_pin
from .dodo import DodoTarget
from .dodo_previews import DodoPreviewRegistry
from .worker import Cancelled


async def command(runtime, run_id, workspace, args, proof, *, timeout=120, input_bytes=None):
    """Bound every owned subprocess and reap only its own detached group."""
    if runtime.cancelled(run_id):
        raise Cancelled()
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=workspace,
        env=(entire_installer_env(workspace) if args[:2] == ["entire", "enable"] else clean_env())
        | {"CI": "1", "NEXT_TELEMETRY_DISABLED": "1"},
        stdin=asyncio.subprocess.PIPE if input_bytes is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    task = asyncio.create_task(process.communicate(input_bytes))
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while not task.done():
            if runtime.cancelled(run_id):
                raise Cancelled()
            if asyncio.get_running_loop().time() > deadline:
                raise RuntimeError("Owned repair command exceeded its allowance; no automatic retry.")
            await asyncio.sleep(0.1)
        stdout, _ = await task
        proof.write_bytes(stdout)
        proof.chmod(0o600)
        return process.returncode, stdout
    finally:
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
            proof.write_bytes(task.result()[0])
            proof.chmod(0o600)


async def checked(runtime, run, workspace, args, label, *, timeout=120):
    code, out = await command(
        runtime,
        run["id"],
        workspace,
        args,
        runtime.evidence / f"{run['repair']['id']}-{label}.log",
        timeout=timeout,
    )
    if code:
        raise RuntimeError(f"Owned Dodo {label} failed; inspect private repair evidence.")
    return out


def metadata(path, value):
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)


async def repair(runtime, run, emit, *, reconcile_only=False):
    from .repair import capture_checkpoint

    service = runtime.service
    with service.store.transaction() as db:
        current = service.store.load(db, "runs", run["id"])
        if current["phase"] == "cancelled":
            raise Cancelled()
        saved = current["repair"]
        finding = service.store.load(db, "findings", saved["finding_id"])
        if not finding.get("measured") or finding["evidence"].get("provider") != "dodo":
            raise ValueError("Dodo source repair requires independently measured Dodo failure evidence.")
        saved["status"] = "running"
        current["repair"] = saved
        service.store.save(db, "repairs", saved)
        service.store.save(db, "runs", current)
    run = {**run, "repair": saved}
    folder = ROOT / "runtime/repairs" / saved["id"]
    workspace = folder / "source"
    owned = OwnedSource(ROOT)
    transcript = runtime.evidence / f"{saved['id']}-codex-repair.jsonl"
    contract_file = folder / "original-source-contract.json"
    if not reconcile_only:
        await runtime.check_revision(run)
        registration = await DodoTarget(run["preview_url"]).registration()
        manifest = registration["manifest"]
        if not registration["ready"] or manifest["mode"] != "broken":
            raise ValueError("Repair requires this configured broken baseline; no mode-only fix is allowed.")
        original = json.loads(
            await checked(
                runtime,
                run,
                owned.target,
                ["npm", "run", "--silent", "repair:contract"],
                "original-contract",
                timeout=20,
            )
        )
        if (
            original["source_commit"] != manifest["source_commit"]
            or original["source_sha256"] != manifest["source_sha256"]
        ):
            raise ValueError("Exported source contract differs from the running measured baseline.")
        pin = owned.snapshot(workspace, expected_digest=manifest["source_sha256"], contract=original)
        pin.update(run_id=run["id"], repair_id=saved["id"], mode=manifest["mode"], original_manifest=manifest)
        write_pin(workspace, pin)
        metadata(contract_file, original)
        # Local metadata is excluded through Git's private info/exclude, without
        # modifying any protected export file or copying provider credentials.
        await checked(
            runtime,
            run,
            workspace,
            ["npm", "ci", "--ignore-scripts", "--cache", str(ROOT / "runtime/npm-cache")],
            "install",
            timeout=120,
        )
        red_code, red_output = await command(
            runtime,
            run["id"],
            workspace,
            ["npm", "run", "test:repair"],
            runtime.evidence / f"{saved['id']}-red.log",
            timeout=60,
        )
        if red_code != 1 or b"fail 2" not in red_output or b"repair guard:" not in red_output:
            raise RuntimeError("Immutable owned paid-access regressions must fail before repair.")
        await checked(
            runtime,
            run,
            workspace,
            ["entire", "enable", "--local", "--agent", "codex", "--skip-push-sessions", "--telemetry=false", "--no-init-repo"],
            "entire-enable",
            timeout=20,
        )
        owned.verify(workspace, pin, require_changed=False)
        summary = {
            key: finding["evidence"].get(key)
            for key in (
                "classification",
                "provider",
                "payment_verified",
                "entitlement_verified",
                "capability_checked",
                "capability_available",
            )
        }
        prompt = (
            "Repair only lib/fulfillment-policy.ts in this owned licensed local source repository. "
            "This is a disclosed controlled paid-but-no-access bug. Independent Dodo Test Mode payment proof is matched to the same account/order/product; confirmation suppresses access in broken mode. "
            "Keep the broken mode and all product/amount/currency configuration unchanged. All payment matching, auth, SQL, read-only oracles, tests, regressions, package files and licensed provenance are protected. "
            "Run the immutable npm run test:repair regression; make the minimal source fix. Never edit tests or any other file. "
            "Do not read outside this workspace, delegate, use network, browse, access keys, push or create remote repos. Leave committing and checkpoint capture to the outer worker. Entire is enabled locally; hooks are disabled for this Codex launch, so the outer worker will attach your exact transcript.\nSanitized finding: "
            + json.dumps(summary)
        )
        code, stdout = await command(
            runtime,
            run["id"],
            workspace,
            [
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
            ],
            transcript,
            timeout=300,
            input_bytes=prompt.encode(),
        )
        if code:
            raise RuntimeError(
                "Codex source repair failed; no automatic retry or configuration substitution."
            )
        owned.verify(workspace, pin, require_changed=True)
        if git(workspace, "status", "--porcelain"):
            git(workspace, "add", SOURCE_FILE)
            git(
                workspace,
                "-c",
                "user.name=Tryfolk Local",
                "-c",
                "user.email=local@tryfolk.invalid",
                "commit",
                "-m",
                "Restore verified Dodo purchase access in controlled broken mode",
            )
    else:
        pin = json.loads((folder / "source-pin.json").read_text())
        stdout = transcript.read_bytes()
        if (
            pin["run_id"] != run["id"]
            or pin["repair_id"] != saved["id"]
            or git(workspace, "status", "--porcelain")
        ):
            raise ValueError("Recovery requires the saved clean repair source; no repair or attach repeated.")
    owned.verify(workspace, pin, require_changed=True)
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith(b"{")]
    session_id = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
    if not session_id or not any(e.get("type") == "turn.completed" for e in events):
        raise RuntimeError("No completed genuine Codex source session for Entire capture.")
    head, checkpoint = await capture_checkpoint(
        workspace,
        session_id,
        runtime.evidence / saved["id"],
        cancelled=lambda: runtime.cancelled(run["id"]),
        reconcile_only=reconcile_only,
    )
    owned.verify(workspace, pin, require_changed=True)
    verification = json.loads(
        await checked(
            runtime,
            run,
            workspace,
            ["npm", "run", "--silent", "repair:contract", "--", "--verify", str(contract_file)],
            "contract-verification",
            timeout=20,
        )
    )
    if verification["valid"] is not True or verification["source_commit"] != head:
        raise ValueError(
            "Official protected source contract did not verify the actual captured repair commit."
        )
    for name, timeout in (("test:repair", 60), ("typecheck", 60), ("build", 120), ("smoke", 30)):
        await checked(
            runtime,
            run,
            workspace,
            ["npm", "run", name],
            "validation-" + name.replace(":", "-"),
            timeout=timeout,
        )
    digest = source_fingerprint(workspace)
    registry = DodoPreviewRegistry(service.store, ROOT / "runtime/repairs")
    preview = registry.request(
        saved["id"],
        source=workspace,
        source_commit=head,
        source_digest=digest,
        mode=pin["mode"],
        run_id=run["id"],
    )
    deadline = asyncio.get_running_loop().time() + 35
    while asyncio.get_running_loop().time() < deadline:
        if runtime.cancelled(run["id"]):
            raise Cancelled()
        preview = registry.get(saved["id"])
        if preview["status"] == "ready":
            break
        if preview["status"] in ("failed", "stopped"):
            raise RuntimeError(preview["error"] or "Owned Dodo preview stopped.")
        await asyncio.sleep(0.2)
    else:
        raise RuntimeError("Owned preview supervisor did not register the exact built Dodo source.")
    registered = preview["manifest"]
    original = pin["original_manifest"]
    for field in ("mode", "provider_environment", "product", "checkout_origins", "upstream_commit"):
        if registered.get(field) != original.get(field):
            raise ValueError("Repaired preview changed protected baseline configuration.")
    from .checkpoint_access import seal_capture_binding

    seal_capture_binding(
        workspace, runtime.evidence / saved["id"], run_id=run["id"], repair_id=saved["id"],
        source_digest=digest, owner_token=service.settings.owner_token,
        repository=service.settings.checkpoint_repository
    )
    service.register_repair(
        saved["id"],
        head_revision=head,
        source_digest=digest,
        target_revision=preview["target_revision"],
        preview_url=preview["origin"],
        checkpoint_id=checkpoint,
        checkpoint_url=None,
        provider="dodo",
    )
    await emit(
        "repair",
        {
            "repair_id": saved["id"],
            "base_revision": run["revision"],
            "head_revision": head,
            "source_digest": digest,
            "target_revision": preview["target_revision"],
            "preview_url": preview["origin"],
            "checkpoint_id": checkpoint,
            "session_id": session_id,
            "mode_unchanged": pin["mode"],
            "allowed_source_files": [SOURCE_FILE],
            "validation": "Official protected-source contract and immutable paid-access regressions, typecheck, exact production build and credential-free HTTP smoke passed",
            "public_checkpoint_gate": "Verified local ID; public accessibility requires separate primary readback.",
        },
    )
