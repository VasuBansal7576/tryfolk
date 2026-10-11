"""Durable preview requests; only the persistent local supervisor owns processes."""

import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
import httpx
from .codex_bridge import clean_env
from .service import now


class PreviewRegistry:
    def __init__(self, store, repairs_root, port_range=range(3102, 3200)):
        self.store = store
        self.repairs_root = Path(repairs_root).resolve()
        self.port_range = port_range
        with store.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")

    def request(self, repair_id, *, source, revision, source_digest):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", repair_id):
            raise ValueError("Invalid repair identity.")
        source = Path(source).resolve()
        expected = self.repairs_root / repair_id / "source" / "fulfillment.py"
        if (
            source != expected
            or not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != source_digest
        ):
            raise ValueError("Preview source must be the exact owned repaired source and digest.")
        with self.store.transaction() as db:
            old = self.store.load(db, "previews", repair_id)
            if old:
                if any(
                    old[k] != v
                    for k, v in {
                        "source": str(source),
                        "revision": revision,
                        "source_digest": source_digest,
                    }.items()
                ):
                    raise ValueError("A registered preview revision is immutable.")
                return old
            used = {
                self.store.load(db, "previews", r[0])["port"] for r in db.execute("SELECT id FROM previews")
            }
            port = next((p for p in self.port_range if p not in used), None)
            if port is None:
                raise ValueError("Owned preview capacity reached.")
            row = {
                "id": repair_id,
                "source": str(source),
                "revision": revision,
                "source_digest": source_digest,
                "port": port,
                "origin": f"http://127.0.0.1:{port}",
                "status": "queued",
                "pid": None,
                "error": None,
                "updated_at": now(),
            }
            db.execute("INSERT INTO previews VALUES (?,?)", (repair_id, __import__("json").dumps(row)))
            return row

    def get(self, preview_id):
        with self.store.transaction() as db:
            return self.store.load(db, "previews", preview_id)

    def all(self):
        with self.store.transaction() as db:
            return [
                self.store.load(db, "previews", r[0])
                for r in db.execute("SELECT id FROM previews").fetchall()
            ]

    def update(self, preview_id, **fields):
        with self.store.transaction() as db:
            row = self.store.load(db, "previews", preview_id)
            row.update(**fields, updated_at=now())
            self.store.save(db, "previews", row)


class PreviewSupervisor:
    def __init__(self, registry, *, fixture_secret, app_dir):
        self.registry = registry
        self.secret = fixture_secret
        self.app_dir = Path(app_dir).resolve()
        self.children = {}
        self.logs = {}
        self.deadlines = {}

    def fail(self, row, message):
        child = self.children.get(row["id"])
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        self.registry.update(row["id"], status="failed", error=message)

    def tick(self):
        for row in self.registry.all():
            source = Path(row["source"])
            if row["status"] not in ("queued", "starting", "ready"):
                continue
            if (
                not source.is_file()
                or hashlib.sha256(source.read_bytes()).hexdigest() != row["source_digest"]
            ):
                self.fail(row, "Pinned preview source changed. Repaired revision invalidated.")
                continue
            if row["status"] == "queued":
                state = source.parents[1] / "state"
                state.mkdir(exist_ok=True)
                self.logs[row["id"]] = (source.parents[1] / "preview.log").open("a")
                env = clean_env() | {
                    "TARGET_STATE": str(state),
                    "TARGET_REVISION": row["revision"],
                    "TARGET_FULFILLMENT": str(source),
                    "TARGET_FAULT": "repaired",
                    "TARGET_ROLE": "app",
                    "TARGET_FIXTURE_SECRET": self.secret,
                }
                child = subprocess.Popen(
                    [
                        str(Path(os.sys.executable)),
                        "-m",
                        "uvicorn",
                        "app:app",
                        "--app-dir",
                        str(self.app_dir),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(row["port"]),
                    ],
                    env=env,
                    stdout=self.logs[row["id"]],
                    stderr=self.logs[row["id"]],
                    start_new_session=True,
                )
                self.children[row["id"]] = child
                self.deadlines[row["id"]] = time.monotonic() + 15
                self.registry.update(row["id"], status="starting", pid=child.pid)
                continue
            child = self.children.get(row["id"])
            if not child:
                self.registry.update(
                    row["id"],
                    status="failed",
                    error="Preview has no process handle owned by this supervisor. Request a scoped restart.",
                )
                continue
            if child.poll() is not None:
                self.fail(
                    row, f"Owned preview exited with code{child.returncode}. No other listener was touched."
                )
                continue
            try:
                health = httpx.get(row["origin"] + "/health", trust_env=False, timeout=0.5)
                value = health.json()
                if (
                    health.status_code != 200
                    or value.get("revision") != row["revision"]
                    or value.get("source_digest") != row["source_digest"]
                    or value.get("provider") != "local-fixture"
                ):
                    self.fail(row, "Preview health does not match exact registered source/revision.")
                    continue
                if row["status"] == "starting":
                    self.registry.update(row["id"], status="ready", error=None)
            except (httpx.HTTPError, ValueError):
                if row["status"] == "ready" or time.monotonic() > self.deadlines[row["id"]]:
                    self.fail(row, "Owned preview failed its health window.")

    def close(self):
        for preview_id, child in self.children.items():
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
            self.registry.update(preview_id, status="stopped", pid=None)
        for log in self.logs.values():
            log.close()
