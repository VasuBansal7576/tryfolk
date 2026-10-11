"""Launch only Tryfolk-owned local processes, with private runtime secrets."""

import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
import hashlib

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"


def start():
    from tryfolk.scenario_registration import bind_registration, load_registration

    scenario_registration = load_registration(os.environ.get("TRYFOLK_SCENARIO_REGISTRATION"))
    RUNTIME.mkdir(exist_ok=True)
    secret_path = RUNTIME / "local-secrets.json"
    if not secret_path.exists():
        secret_path.write_text(
            json.dumps({"fixture": secrets.token_urlsafe(32), "owner": secrets.token_urlsafe(32)})
        )
        secret_path.chmod(0o600)
    keys = json.loads(secret_path.read_text())
    if "dodo" not in keys:
        keys["dodo"] = secrets.token_urlsafe(32)
        secret_path.write_text(json.dumps(keys))
    healthy = RUNTIME / "healthy-fulfillment.py"
    healthy.write_text('def should_grant(payment):\n    return payment["status"] == "succeeded"\n')
    broken = ROOT / "owned-target/fixture/fulfillment.py"
    connections = []
    launches = [
        ("provider", 3200, healthy, "healthy"),
        ("healthy", 3100, healthy, "healthy"),
        ("broken", 3101, broken, "paid-no-access"),
    ]
    from tryfolk.store import Store
    from tryfolk.previews import PreviewRegistry, PreviewSupervisor

    registry = PreviewRegistry(Store(RUNTIME / "tryfolk.sqlite"), RUNTIME / "repairs")
    # Explicit local launch restores completed owned previews, never resumes browser jobs.
    with registry.store.transaction() as db:
        repairs = [json.loads(row[0]) for row in db.execute("SELECT payload FROM repairs").fetchall()]
    for repair in repairs:
        if (
            repair["status"] == "completed"
            and repair.get("source_digest")
            and repair.get("provider", "local-fixture") == "local-fixture"
        ):
            preview = registry.request(
                repair["id"],
                source=RUNTIME / "repairs" / repair["id"] / "source/fulfillment.py",
                revision=repair["head_revision"],
                source_digest=repair["source_digest"],
            )
            if preview["status"] in ("stopped", "failed"):
                registry.update(preview["id"], status="queued", pid=None, error=None)
    previews = PreviewSupervisor(
        registry, fixture_secret=keys["fixture"], app_dir=ROOT / "owned-target/fixture"
    )
    children = []
    for name, port, source, fault in launches:
        revision = hashlib.sha256(source.read_bytes()).hexdigest()
        env = os.environ | {
            "TARGET_STATE": str(RUNTIME / name),
            "TARGET_REVISION": revision,
            "TARGET_FULFILLMENT": str(source),
            "TARGET_ROLE": "provider" if name == "provider" else "app",
            "TARGET_FIXTURE_SECRET": keys["fixture"],
            "TARGET_FAULT": fault,
        }
        log = (RUNTIME / f"{name}.log").open("a")
        process = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "app:app",
                "--app-dir",
                str(ROOT / "owned-target/fixture"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        children.append(process)
        if name != "provider":
            connections.append(
                {
                    "url": f"http://127.0.0.1:{port}",
                    "revision": revision,
                    "fault": fault,
                    "provider": "local-fixture",
                    "repo_path": str(ROOT / "owned-target/fixture"),
                }
            )
    (RUNTIME / "connections.json").write_text(json.dumps(connections, indent=2))
    dodo_root = ROOT / "dodo-target"
    from dotenv import dotenv_values

    private = {
        key: value for key, value in dotenv_values(dodo_root / ".env.local").items() if value is not None
    }
    control = private.get("TARGET_CONTROL_TOKEN", keys["dodo"])
    from tryfolk.dodo_previews import DodoPreviewRegistry, DodoPreviewSupervisor

    dodo_registry = DodoPreviewRegistry(registry.store, RUNTIME / "repairs")
    # Explicit launch restores registered exact source builds; no payment jobs
    # are resumed and only this supervisor owns the launched process handles.
    for preview in dodo_registry.all():
        if preview["status"] in ("starting", "ready", "stopped"):
            dodo_registry.update(preview["id"], status="queued", pid=None, error=None)
    dodo_previews = DodoPreviewSupervisor(dodo_registry, private | {"TARGET_CONTROL_TOKEN": control})
    if (dodo_root / ".next/BUILD_ID").exists():
        import httpx
        for mode, port in [("healthy", 3300), ("broken", 3301)]:
            origin = f"http://127.0.0.1:{port}"
            env = (
                os.environ
                | private
                | {
                    "NODE_ENV": "production",
                    "TARGET_ENV": "local",
                    "TARGET_SANDBOX": "true",
                    "TARGET_ORIGIN": origin,
                    "TARGET_CONTROL_TOKEN": control,
                    "TARGET_MODE": mode,
                    "TARGET_DATABASE_PATH": str(RUNTIME / f"dodo-{mode}.sqlite"),
                }
            )
            log = (RUNTIME / f"dodo-{mode}.log").open("a")
            process = subprocess.Popen(
                [
                    "node",
                    "node_modules/next/dist/bin/next",
                    "start",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=dodo_root,
                env=env,
                stdout=log,
                stderr=log,
            )
            children.append(process)
            deadline = time.monotonic() + 30
            while True:
                try:
                    response = httpx.get(origin + "/api/target/readiness", trust_env=False, timeout=2)
                    response.raise_for_status()
                    readiness = response.json()
                    manifest = readiness["manifest"]
                    from tryfolk.target_provenance import dodo_provenance
                    from tryfolk.dodo_runtime_validation import execution_ready
                    if manifest["source_clean"] is not True or manifest["app_origin"] != origin:
                        raise RuntimeError("Dodo build provenance or exact origin failed.")
                    connections.append(
                        {
                            "url": origin,
                            "revision": manifest["target_revision"],
                            "fault": manifest["fault"] or "healthy",
                            "provider": "dodo",
                            "repo_path": str(dodo_root),
                            "allowed_origins": [origin],
                            "source_commit": manifest["source_commit"],
                            "source_digest": manifest["source_sha256"],
                            "mode": manifest["mode"],
                            "execution_ready": execution_ready(manifest, RUNTIME / "dodo-execution-validation.json", ROOT),
                            "target_provenance": dodo_provenance(manifest, dodo_root),
                            **bind_registration(manifest, scenario_registration),
                            "setup_gates": [
                                {
                                    "code": "dodo_target_" + e["code"],
                                    "message": e["code"].replace("_", " "),
                                    "action": e["action"],
                                }
                                for e in readiness["errors"]
                            ],
                        }
                    )
                    (RUNTIME / f"dodo-{mode}-readiness.json").write_text(json.dumps(readiness, indent=2))
                    break
                except (httpx.HTTPError, OSError):
                    if time.monotonic() > deadline or process.poll() is not None:
                        raise RuntimeError(
                            "Owned Dodo preview failed startup; inspect its private runtime log."
                        )
                    time.sleep(0.25)
        # The genuinely configured owned Dodo sandbox is the product's connected
        # target. Historical fixture records and their origins remain registered.
        connections.sort(key=lambda row: not (
            row["provider"] == "dodo" and row.get("mode") == "broken" and row.get("execution_ready") is True
        ))
        (RUNTIME / "connections.json").write_text(json.dumps(connections, indent=2))
    api_env = os.environ | {
        "TRYFOLK_OWNER_TOKEN": keys["owner"],
        "TARGET_FIXTURE_SECRET": keys["fixture"],
        "TRYFOLK_CONNECTIONS": str(RUNTIME / "connections.json"),
    }
    if (dodo_root / ".env.local").exists():
        api_env["TARGET_CONTROL_TOKEN"] = control
    log = (RUNTIME / "api.log").open("a")
    process = subprocess.Popen(
        [
            str(ROOT / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "tryfolk.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=ROOT,
        env=api_env,
        stdout=log,
        stderr=log,
    )
    children.append(process)
    (RUNTIME / "pids.json").write_text(json.dumps([p.pid for p in children]))
    print(
        "Tryfolk API http://127.0.0.1:8000/docs | healthy http://127.0.0.1:3100 | broken http://127.0.0.1:3101",
        flush=True,
    )

    def close(*args):
        previews.close()
        dodo_previews.close()
        for child in children:
            child.terminate()
        for child in children:
            child.wait(timeout=15)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, close)
    signal.signal(signal.SIGINT, close)
    while all(p.poll() is None for p in children):
        previews.tick()
        dodo_previews.tick()
        time.sleep(0.25)
    close()


if __name__ == "__main__":
    start()
