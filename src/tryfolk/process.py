"""Bounded SDK JSONL processes. Cancellation kills their own process group only."""

import asyncio
import json
import os
from pathlib import Path
import signal
from .codex_bridge import clean_env
from .worker import Cancelled

ROOT = Path(__file__).resolve().parents[2]
RESPONSE_LIMIT = 2_000_000


class SDKProcess:
    def __init__(self, name, cancelled=lambda: False, *, frame_verify=None, evidence_root=None):
        self.name = name
        self.cancelled = cancelled
        self.frame_verify = frame_verify
        self.evidence_root = Path(
            evidence_root or os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")
        ).resolve()
        self.process = None
        self.request_lock = asyncio.Lock()

    async def start(self):
        env = clean_env() | {
            "PYTHONPATH": str(ROOT / "src"),
            "ANONYMIZED_TELEMETRY": "false",
            "BROWSER_USE_TELEMETRY": "false",
            "TRYFOLK_EVIDENCE_ROOT": str(self.evidence_root),
        }
        working = ROOT / "runtime" / "sdk"
        working.mkdir(parents=True, exist_ok=True)
        self.log = (working / f"{self.name}.log").open("a")
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
                raise RuntimeError(f"{self.name} SDK process exited; inspect runtime/sdk/{self.name}.log")
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
