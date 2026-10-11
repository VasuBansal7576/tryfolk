"""Live cancellation of one real Browser Use discovery; no payment actions."""

import asyncio
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
from tryfolk.api import create_app
from tryfolk.config import Settings
from tryfolk.runtime import Runtime
from tryfolk.worker import Worker


async def main():
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TRYFOLK_OWNER_TOKEN"] = keys["owner"]
    os.environ["TARGET_FIXTURE_SECRET"] = keys["fixture"]
    client = TestClient(create_app(Settings.from_env()))
    quote = client.post("/quotes", json={"app_url": "http://127.0.0.1:3100", "persona_count": 1}).json()
    response = client.post(
        "/runs", json={"quote_id": quote["id"]}, headers={"Idempotency-Key": "live-cancel:" + quote["id"]}
    )
    response.raise_for_status()
    created = response.json()
    worker = Worker(client.app.state.service, Runtime(client.app.state.service))
    task = asyncio.create_task(worker.tick())
    auth = {"Authorization": "Bearer " + created["access_token"]}
    deadline = asyncio.get_running_loop().time() + 15
    while client.get("/runs/" + created["id"], headers=auth).json()["phase"] != "discovering":
        if asyncio.get_running_loop().time() > deadline:
            raise RuntimeError("Cancellation probe job was not claimed.")
        await asyncio.sleep(0.1)
    await asyncio.sleep(2)
    cancelled = client.post("/runs/" + created["id"] + "/cancel", headers=auth)
    cancelled.raise_for_status()
    await asyncio.wait_for(task, 15)
    final = client.get("/runs/" + created["id"], headers=auth).json()
    assert (
        final["phase"] == "cancelled"
        and final["counts"]["executed"] == 0
        and final["counts"]["generated"] == 0
    )
    assert (
        final["budget"]["estimated_consumed_microusd"] == 10_000
        and final["budget"]["released_microusd"] == 80_000
    )
    evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")) / "live-cancel.json"
    evidence.write_text(json.dumps(final, indent=2))
    print(
        json.dumps(
            {
                "id": final["id"],
                "phase": final["phase"],
                "executed": final["counts"]["executed"],
                "budget": final["budget"],
                "evidence": str(evidence),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
