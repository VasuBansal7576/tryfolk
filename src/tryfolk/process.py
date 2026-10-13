"""Bounded SDK JSONL processes. Cancellation kills their own process group only."""

import asyncio
import json
import os
from pathlib import Path
import signal
from uuid import uuid4
from .codex_bridge import clean_env
from .worker import Cancelled

ROOT = Path(__file__).resolve().parents[2]
RESPONSE_LIMIT = 2_000_000


class SDKProcess:
    def __init__(self, name, cancelled=lambda: False, *, frame_verify=None, evidence_root=None, reuse_browser=False):
        self.name = name
        self.cancelled = cancelled
        self.frame_verify = frame_verify
        self.reuse_browser = reuse_browser
        self.evidence_root = Path(
            evidence_root or os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")
        ).resolve()
        self.process = None
        self.worker_id = uuid4().hex
        self.working = ROOT / "runtime" / "sdk" / (name + "-" + self.worker_id)
        self.request_lock = asyncio.Lock()

    async def start(self):
        env = clean_env() | {
            "PYTHONPATH": str(ROOT / "src"),
            "ANONYMIZED_TELEMETRY": "false",
            "BROWSER_USE_TELEMETRY": "false",
            "TRYFOLK_EVIDENCE_ROOT": str(self.evidence_root),
        }
        env["TRYFOLK_STAGEHAND_REUSE"] = "true" if self.reuse_browser else "false"
        working = self.working
        working.mkdir(parents=True, exist_ok=True)
        os.chmod(working, 0o700)
        self.log = (working / f"{self.name}.log").open("a")
        os.chmod(self.log.name, 0o600)
        self.process = await asyncio.create_subprocess_exec(
            str(ROOT / f"integrations/{self.name}/.venv/bin/python"),
            str(ROOT / f"integrations/{self.name}/adapter.py"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self.log,
            cwd=working,
            env=env,
            start_new_session=True,
            limit=RESPONSE_LIMIT,
        )

    async def request(self, data, timeout=180):
        async with self.request_lock:
            return await self._request(data, timeout)

    async def _request(self, data, timeout=180):
        if not self.process:
            await self.start()
        if self.cancelled():
            raise Cancelled()
        self.process.stdin.write((json.dumps(data) + "\n").encode())
        await self.process.stdin.drain()
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.cancelled():
                await self.close()
                raise Cancelled()
            try:
                line = await asyncio.wait_for(self.process.stdout.readline(), timeout=0.25)
            except TimeoutError:
                continue
            except ValueError as error:
                await self.close()
                raise RuntimeError(
                    f"{self.name} SDK response exceeds the {RESPONSE_LIMIT}-byte limit; ambiguous actions are not retried."
                ) from error
            if not line:
                raise RuntimeError(f"{self.name} SDK process exited; inspect {self.log.name}")
            try:
                result = json.loads(line)
            except ValueError:
                continue
            if result.get("callback") == "verify_dodo_frame" and self.name == "stagehand":
                decision = {"approved": False, "reason": "independent_frame_context_unavailable"}
                if self.frame_verify:
                    task = asyncio.create_task(self.frame_verify(result.get("data") or {}))
                    try:
                        callback_deadline = min(deadline, asyncio.get_running_loop().time() + 12)
                        while not task.done() and asyncio.get_running_loop().time() < callback_deadline:
                            if self.cancelled():
                                await self.close()
                                raise Cancelled()
                            await asyncio.wait({task}, timeout=0.25)
                        if task.done():
                            decision = task.result()
                    except Cancelled:
                        raise
                    except Exception:
                        pass  # No controller credentials or errors cross this boundary.
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.process.stdin.write(
                    (json.dumps({"callback_reply": result.get("call_id"), "data": decision}) + "\n").encode()
                )
                await self.process.stdin.drain()
                continue
            if "ok" not in result:
                continue
            if not result["ok"]:
                error = RuntimeError(f"{self.name}: {result['error']}")
                error.evidence_path = result.get("error_evidence_path")
                error.sdk_code = result.get("code")
                error.model_invoked = result.get("model_invoked")
                error.sdk_data = result.get("data")
                if self.name == "stagehand":
                    error.pre_submission_evidence = result.get("pre_submission_evidence")
                raise error
            return result["data"]
        await self.close()
        raise RuntimeError(f"{self.name} timeout; ambiguous actions are not retried.")

    async def close(self):
        if self.process and self.process.returncode is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except TimeoutError:
                os.killpg(self.process.pid, signal.SIGKILL)
                await self.process.wait()
        if hasattr(self, "log"):
            self.log.close()


class PersonaChannel:
    """Persona-bound commands through one run-owned persistent TinyTroupe service."""

    def __init__(self, service, run_id, persona_id):
        self.service, self.run_id, self.persona_id = service, run_id, persona_id
        self.initialized = False

    @property
    def process(self):
        return self.service.process

    async def request(self, data, timeout=180):
        value = await self.service.request(
            {**data, "op": "bulk_" + data["op"],
             "run_id": self.run_id, "persona_id": self.persona_id}, timeout,
        )
        if data["op"] == "init":
            self.initialized = True
        return value

    async def close(self):
        if self.initialized and self.process and self.process.returncode is None:
            await self.request({"op": "release"})
            self.initialized = False


class PersonaPool:
    """Run-owned decision lanes, each leased for one persona's entire journey."""

    def __init__(self, size, run_id, cancelled=lambda: False, *, evidence_root=None):
        if size < 1:
            raise ValueError("At least one decision lane is required.")
        self.run_id = run_id
        self.cancelled = cancelled
        self.evidence_root = evidence_root
        self.available = asyncio.Queue()
        self.services = []
        self.closed = False
        for _ in range(size):
            self.available.put_nowait(self._new_service())

    def _new_service(self):
        service = SDKProcess("tinytroupe", self.cancelled, evidence_root=self.evidence_root)
        self.services.append(service)
        return service

    async def acquire(self, persona_id):
        while True:
            if self.closed:
                raise RuntimeError("Decision pool is already closed.")
            if self.cancelled():
                raise Cancelled()
            try:
                service = await asyncio.wait_for(self.available.get(), 0.25)
            except TimeoutError:
                continue
            # Closing/cancellation can happen while a lane becomes available.
            if self.closed or self.cancelled():
                self.available.put_nowait(service)
                if self.closed:
                    raise RuntimeError("Decision pool is already closed.")
                raise Cancelled()
            return PersonaLease(self, service, persona_id)

    def release(self, service, reusable):
        if not self.closed:
            self.available.put_nowait(service if reusable else self._new_service())

    async def close(self):
        self.closed = True
        await asyncio.gather(*(service.close() for service in self.services))


class PersonaLease(PersonaChannel):
    """A persona retains one lane until its state is explicitly released."""

    def __init__(self, pool, service, persona_id):
        super().__init__(service, pool.run_id, persona_id)
        self.pool = pool
        self.broken = False
        self.closed = False

    async def request(self, data, timeout=180):
        if self.closed or self.pool.closed:
            raise RuntimeError("Owned persona lease is already closed.")
        try:
            return await super().request(data, timeout)
        except BaseException:
            # An interrupted JSONL exchange may leave an unread reply or partly
            # initialized memory. Never hand that process to another customer.
            self.broken = True
            raise

    async def close(self):
        if self.closed:
            return
        self.closed = True
        reusable = False
        try:
            if not self.pool.closed and not self.broken:
                if self.initialized:
                    receipt = await self.service.request({
                        "op": "bulk_release", "run_id": self.run_id, "persona_id": self.persona_id,
                    }, timeout=20)
                    if receipt.get("released") is not True:
                        raise RuntimeError("Persona state release receipt is required before lane reuse.")
                    self.initialized = False
                reusable = True
        finally:
            if not reusable:
                await self.service.close()
            self.pool.release(self.service, reusable)


class BrowserLease:
    """One persona's fresh context; failed lifecycle hosts are quarantined."""

    def __init__(self, service, release):
        self.service, self.release = service, release
        self.started = False
        self.broken = False
        self.closed = False

    async def request(self, data, timeout=180):
        if self.closed:
            raise RuntimeError("Owned browser lease is already closed.")
        if data["op"] == "goto":
            data = {**data, "op": "begin_session"}
            # The adapter may allocate a context before goto/guard settlement.
            self.started = True
        try:
            return await self.service.request(data, timeout)
        except BaseException:
            self.broken = True
            raise

    async def close(self):
        if self.closed:
            return
        self.closed = True
        reusable = False
        try:
            if self.started and not self.broken and self.service.process and self.service.process.returncode is None:
                receipt = await self.service.request({"op": "end_session"}, timeout=20)
                if receipt.get("fresh_context_closed") is not True:
                    raise RuntimeError("Actual owned context close receipt is required before host reuse.")
                reusable = True
        finally:
            self.service.frame_verify = None
            if not reusable:
                await self.service.close()
            self.release(self.service, reusable)
