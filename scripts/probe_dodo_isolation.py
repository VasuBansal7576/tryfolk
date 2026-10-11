"""Live owned Dodo auth/database isolation diagnostic; no provider calls or personas."""

import asyncio
import json
import os
from pathlib import Path
from tryfolk.dodo import DodoTarget


async def main():
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TARGET_CONTROL_TOKEN"] = keys["dodo"]
    proof = []
    for mode, port in [("healthy", 3300), ("broken", 3301)]:
        database = Path("runtime/dodo-" + mode + ".sqlite")
        assert database.exists(), "Supervisor did not bind the required distinct " + mode + " database."
        api = DodoTarget("http://127.0.0.1:" + str(port))
        readiness = await api.registration()
        revision = readiness["manifest"]["target_revision"]
        run = {"id": "local-isolation-probe-" + mode, "revision": revision}
        seed = await api.provision(run, {"id": "diagnostic-not-persona"})
        reset = await api.request(
            "POST", "/api/target/reset", json={"run_id": run["id"], "target_revision": revision}
        )
        assert reset["revoked_accounts"] == 1 and reset["revoked_orders"] == 0
        proof.append(
            {
                "origin": api.origin,
                "database": str(database.resolve()),
                "target_revision": revision,
                "account_id": seed["account_id"],
                "fixture_id": seed["fixture_id"],
                "reset": reset,
                "diagnostic": True,
                "provider_baseline_verified": False,
                "not_in_population_denominator": True,
            }
        )
    assert proof[0]["database"] != proof[1]["database"] and proof[0]["account_id"] != proof[1]["account_id"]
    path = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")) / "dodo-live-isolation.json"
    path.write_text(json.dumps(proof, indent=2))
    print(json.dumps({"owned_preview_isolation_verified": True, "provider_calls": 0, "evidence": str(path)}))


if __name__ == "__main__":
    asyncio.run(main())
