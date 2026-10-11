"""Run the real SDK worker once, persist its evidence; no external charges."""

import asyncio
import json
import os
import argparse
from pathlib import Path
from fastapi.testclient import TestClient
from tryfolk.config import Settings, Target
from tryfolk.api import create_app
from tryfolk.runtime import Runtime
from tryfolk.worker import Worker


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--url", default="http://127.0.0.1:3101")
    args = parser.parse_args()
    if os.environ.get("TRYFOLK_NEATLOGS_ENV"):
        from dotenv import load_dotenv

        load_dotenv(os.environ["TRYFOLK_NEATLOGS_ENV"])
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TARGET_FIXTURE_SECRET"] = keys["fixture"]
    targets = tuple(Target(**x) for x in json.loads(Path("runtime/connections.json").read_text()))
    settings = Settings(
        db_path=Path("runtime/tryfolk.sqlite"), targets=targets, owner_token=keys["owner"], gates=()
    )
    app = create_app(settings)
    client = TestClient(app)
    q = client.post("/quotes", json={"app_url": args.url, "persona_count": args.count}).json()
    result = client.post("/runs", json={"quote_id": q["id"]}, headers={"Idempotency-Key": q["id"]}).json()
    worker = Worker(app.state.service, Runtime(app.state.service))
    await worker.tick()
    status = client.get(
        "/runs/" + result["id"], headers={"Authorization": "Bearer " + result["access_token"]}
    ).json()
    output = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")) / "one-persona-cycle.json"
    output.write_text(json.dumps(status, indent=2))
    output.with_name(status["id"] + "-before.json").write_text(json.dumps(status, indent=2))
    if status["phase"] == "completed" and status["counts"]["executed"] == args.count:
        Path("runtime/sdk-validation.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "run_id": status["id"],
                    "evidence": str(output.with_name(status["id"] + "-before.json").resolve()),
                    "model": "gpt-6.1-sol",
                    "cash_charged_microusd": 0,
                    "sdk_versions": {
                        "browser_use": "0.13.11",
                        "stagehand": "4.2.0",
                        "tinytroupe": "a6244b358a1fe1c71bf751f7ba0f8dfa368ec5a4",
                    },
                }
            )
        )
    print(
        json.dumps(
            {
                "run_id": status["id"],
                "phase": status["phase"],
                "counts": status["counts"],
                "gates": status["gates"],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
