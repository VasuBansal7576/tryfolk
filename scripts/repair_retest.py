"""Owner-only repair and exact-population replay through the public API."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
from tryfolk.config import Settings, Target
from tryfolk.api import create_app
from tryfolk.runtime import Runtime
from tryfolk.worker import Worker


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    if os.environ.get("TRYFOLK_NEATLOGS_ENV"):
        from dotenv import load_dotenv

        load_dotenv(os.environ["TRYFOLK_NEATLOGS_ENV"])
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TARGET_FIXTURE_SECRET"] = keys["fixture"]
    targets = tuple(Target(**x) for x in json.loads(Path("runtime/connections.json").read_text()))
    app = create_app(
        Settings(db_path=Path("runtime/tryfolk.sqlite"), targets=targets, owner_token=keys["owner"], gates=())
    )
    client = TestClient(app)
    auth = {"Authorization": "Bearer " + keys["owner"]}
    worker = Worker(app.state.service, Runtime(app.state.service))
    before = client.get("/runs/" + args.run_id, headers=auth)
    before.raise_for_status()
    if before.json()["phase"] != "completed":
        raise RuntimeError("Baseline must complete; blocked is not a finding.")
    findings = client.get("/runs/" + args.run_id + "/findings", headers=auth).json()["findings"]
    if not findings:
        raise RuntimeError("No independently correlated failure exists.")
    r = client.post("/runs/" + args.run_id + "/repairs", headers=auth, json={"finding_id": findings[0]["id"]})
    r.raise_for_status()
    repair_id = r.json()["id"]
    await worker.tick()
    b = client.get("/runs/" + args.run_id, headers=auth).json()
    evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
    (evidence / (args.run_id + "-repair-status.json")).write_text(json.dumps(b, indent=2))
    if b["repair"]["status"] != "completed":
        print(json.dumps({"repair": b["repair"]}))
        return
    r = client.post(
        "/runs/" + args.run_id + "/replay",
        headers=auth | {"Idempotency-Key": "replay:" + repair_id},
        json={"repair_id": repair_id},
    )
    r.raise_for_status()
    after_id = r.json()["id"]
    await worker.tick()
    after = client.get("/runs/" + after_id, headers=auth).json()
    (evidence / (after_id + "-after.json")).write_text(json.dumps(after, indent=2))
    print(
        json.dumps(
            {
                "before_id": args.run_id,
                "after_id": after_id,
                "phase": after["phase"],
                "comparison": after["comparison"],
                "gates": after["gates"],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
