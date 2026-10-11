"""Persistent ownership of exact built Dodo source previews and private databases."""

import json
from pathlib import Path
import re
import subprocess
import time
import httpx
from .codex_bridge import clean_env
from .service import now
from .source_workspace import git, source_fingerprint


class DodoPreviewRegistry:
    def __init__(self, store, repairs_root, port_range=range(3310, 3400)):
        self.store, self.repairs_root, self.port_range = store, Path(repairs_root).resolve(), port_range
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS dodo_previews(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")

    def request(self, repair_id, *, source, source_commit, source_digest, mode, run_id):
        source = Path(source).resolve()
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]+", repair_id)
            or source != self.repairs_root / repair_id / "source"
            or not re.fullmatch(r"[a-f0-9]{40}", source_commit)
            or mode not in ("healthy", "broken")
        ):
            raise ValueError("Dodo preview requires its exact owned repair workspace and baseline mode.")
        self.validate_source(source, source_commit, source_digest)
        with self.store.transaction() as db:
            old = self.store.load(db, "dodo_previews", repair_id)
            identity = {
                "source": str(source),
                "source_commit": source_commit,
                "source_digest": source_digest,
                "mode": mode,
                "run_id": run_id,
            }
            if old:
                if any(old[k] != v for k, v in identity.items()):
                    raise ValueError("A registered Dodo source preview is immutable.")
                return old
            used = {json.loads(r[0])["port"] for r in db.execute("SELECT payload FROM dodo_previews")}
            port = next((p for p in self.port_range if p not in used), None)
            if port is None:
                raise ValueError("Owned Dodo preview capacity reached.")
            row = {
                "id": repair_id,
                **identity,
                "port": port,
                "origin": f"http://127.0.0.1:{port}",
                "status": "queued",
                "pid": None,
                "target_revision": None,
                "error": None,
                "updated_at": now(),
            }
            db.execute("INSERT INTO dodo_previews VALUES (?,?)", (repair_id, json.dumps(row)))
            return row

    @staticmethod
    def validate_source(source, commit, digest):
        if git(source, "rev-parse", "HEAD") != commit or git(source, "status", "--porcelain"):
            raise ValueError("Dodo source preview is not the exact clean registered commit.")
        built = json.loads((source / ".target-manifest.json").read_text())
        if (
            built != {"source_sha256": digest, "source_commit": commit, "source_clean": True}
            or not (source / ".next/BUILD_ID").is_file()
            or source_fingerprint(source) != digest
        ):
            raise ValueError("Built Dodo preview source digest/commit differs from registration.")

    def get(self, repair_id):
        with self.store.transaction() as db:
            return self.store.load(db, "dodo_previews", repair_id)

    def all(self):
        with self.store.transaction() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM dodo_previews").fetchall()]

    def update(self, repair_id, **fields):
        with self.store.transaction() as db:
            row = self.store.load(db, "dodo_previews", repair_id)
            row.update(**fields, updated_at=now())
            self.store.save(db, "dodo_previews", row)


class DodoPreviewSupervisor:
    def __init__(self, registry, private_env):
        self.registry, self.private_env = registry, dict(private_env)
        self.children, self.logs, self.deadlines = {}, {}, {}

    def stop(self, row, *, status="failed", error=None):
        child = self.children.get(row["id"])
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        self.registry.update(row["id"], status=status, pid=None, error=error)

    def tick(self):
        for row in self.registry.all():
            if row["status"] not in ("queued", "starting", "ready"):
                continue
            with self.registry.store.transaction() as db:
                run = self.registry.store.load(db, "runs", row["run_id"])
                cancelled = run and run["phase"] == "cancelled"
                if run and run.get("reservation_id"):
                    related = [json.loads(r[0]) for r in db.execute("SELECT payload FROM runs")]
                    cancelled = cancelled or any(
                        candidate.get("reservation_id") == run["reservation_id"]
                        and candidate["phase"] == "cancelled"
                        for candidate in related
                    )
            if cancelled:
                self.stop(row, status="stopped", error="Owner cancelled this repair run.")
                continue
            source = Path(row["source"])
            try:
                self.registry.validate_source(source, row["source_commit"], row["source_digest"])
            except (ValueError, OSError, subprocess.SubprocessError):
                self.stop(row, error="Exact built Dodo source changed; preview acceptance invalidated.")
                continue
            if row["status"] == "queued":
                self.logs[row["id"]] = (source.parent / "preview.log").open("a")
                env = (
                    clean_env()
                    | self.private_env
                    | {
                        "NODE_ENV": "production",
                        "TARGET_ENV": "local",
                        "TARGET_SANDBOX": "true",
                        "TARGET_ORIGIN": row["origin"],
                        "TARGET_MODE": row["mode"],
                        "TARGET_DATABASE_PATH": str(source.parent / "preview.sqlite"),
                    }
                )
                child = subprocess.Popen(
                    [
                        "node",
                        "node_modules/next/dist/bin/next",
                        "start",
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        str(row["port"]),
                    ],
                    cwd=source,
                    env=env,
                    stdout=self.logs[row["id"]],
                    stderr=self.logs[row["id"]],
                    start_new_session=True,
                )
                self.children[row["id"]] = child
                self.deadlines[row["id"]] = time.monotonic() + 30
                self.registry.update(row["id"], status="starting", pid=child.pid)
                continue
            child = self.children.get(row["id"])
            if not child or child.poll() is not None:
                self.stop(row, error="Registered Dodo preview has no live process owned by this supervisor.")
                continue
            try:
                response = httpx.get(row["origin"] + "/api/target/readiness", trust_env=False, timeout=1)
                response.raise_for_status()
                readiness = response.json()
                manifest = readiness["manifest"]
                if (
                    not readiness["ready"]
                    or manifest["source_commit"] != row["source_commit"]
                    or manifest["source_sha256"] != row["source_digest"]
                    or manifest["source_clean"] is not True
                    or manifest["app_origin"] != row["origin"]
                    or manifest["safe_origins"] != [row["origin"]]
                    or manifest["mode"] != row["mode"]
                    or manifest["provider_environment"] != "test_mode"
                    or manifest["checkout_origins"] != ["https://test.checkout.dodopayments.com"]
                    or (
                        row["target_revision"] is not None
                        and row["target_revision"] != manifest["target_revision"]
                    )
                ):
                    raise ValueError("Dodo preview readiness differs from exact registered source/config.")
                self.registry.update(
                    row["id"],
                    status="ready",
                    target_revision=manifest["target_revision"],
                    manifest=manifest,
                    error=None,
                )
            except (httpx.HTTPError, ValueError, KeyError):
                if row["status"] == "ready" or time.monotonic() > self.deadlines[row["id"]]:
                    self.stop(row, error="Dodo preview failed its exact-source readiness window.")

    def close(self):
        for repair_id in self.children:
            self.stop(self.registry.get(repair_id), status="stopped")
        for log in self.logs.values():
            log.close()
