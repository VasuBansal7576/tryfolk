"""Actual owned-fixture HTTP controls, separately counted from persona N.
No LLM reasoning or Dodo payment is represented by these checks.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from tryfolk.config import Settings
from tryfolk.service import Service
from tryfolk.runtime import Runtime


from tryfolk.controls import check


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TARGET_FIXTURE_SECRET"] = keys["fixture"]
    os.environ["TRYFOLK_OWNER_TOKEN"] = keys["owner"]
    service = Service(Settings.from_env())
    run = service.get_run(args.run_id, keys["owner"])
    if run["provider"] != "local-fixture":
        raise RuntimeError("These controls are fixture-only, not Dodo checks.")
    result = await check(Runtime(service), run)
    path = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")) / (args.run_id + "-heldout.json")
    path.write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {"heldout_cases": len(result), "passed": sum(r["passed"] for r in result), "evidence": str(path)}
        )
    )
    if not all(r["passed"] for r in result):
        raise RuntimeError("Held-out control failed.")


if __name__ == "__main__":
    asyncio.run(main())
