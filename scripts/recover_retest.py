"""Explicit owner recovery of unpaid local diagnostics; never retries a paid journey."""

import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TRYFOLK_OWNER_TOKEN"] = keys["owner"]
    os.environ["TARGET_FIXTURE_SECRET"] = keys["fixture"]
    if os.environ.get("TRYFOLK_NEATLOGS_ENV"):
        from dotenv import load_dotenv

        load_dotenv(os.environ["TRYFOLK_NEATLOGS_ENV"])
    app = create_app(Settings.from_env())
    client = TestClient(app)
    auth = {"Authorization": "Bearer " + keys["owner"]}
    response = client.post("/runs/" + args.run_id + "/recover", headers=auth)
    response.raise_for_status()
    print(
        json.dumps({"recovery": response.json()["recovery"], "budget": response.json()["budget"]}), flush=True
    )
    await Worker(app.state.service, Runtime(app.state.service)).tick()
    result = client.get("/runs/" + args.run_id, headers=auth).json()
    evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
    (evidence / (args.run_id + "-after.json")).write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "id": args.run_id,
                "phase": result["phase"],
                "comparison": result["comparison"],
                "gates": result["gates"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
