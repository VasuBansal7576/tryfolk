"""Real Stagehand regression for immediate known-action sequencing. Fixture only."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from tryfolk.config import Settings
from tryfolk.service import Service
from tryfolk.runtime import Runtime
from tryfolk.process import SDKProcess


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TARGET_FIXTURE_SECRET"] = keys["fixture"]
    os.environ["TRYFOLK_OWNER_TOKEN"] = keys["owner"]
    svc = Service(Settings.from_env())
    run = svc.get_run(args.run_id, keys["owner"])
    runtime = Runtime(svc)
    with svc.store.transaction() as db:
        before = svc.store.load(db, "runs", run["parent_run_id"])
    failure = next(v for v in before["outcomes"].values() if v["outcome"] == "failed")
    seed = await runtime.provision(run, run["personas"][0])
    browser = SDKProcess("stagehand")
    actions = []
    try:
        await browser.request({"op": "goto", "url": run["preview_url"] + seed["session_url"]})
        for action in failure["actions"]:
            result = await browser.request({"op": "act", "action": action})
            actions.append({"action": action, "result": result})
        verdict = await runtime.verification(run, seed)
        evidence = {
            "case": "immediate_known_action_sequence",
            "actions": actions,
            "verification": verdict,
            "diagnostic": True,
            "not_in_population_denominator": True,
        }
        out = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")) / "known-sequence-probe.json"
        out.write_text(json.dumps(evidence, indent=2))
        print(
            json.dumps(
                {
                    "outcome": verdict["outcome"],
                    "classification": verdict["classification"],
                    "evidence": str(out),
                }
            )
        )
        assert verdict["outcome"] == "completed", "Immediate action replay lost the purchase navigation."
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
