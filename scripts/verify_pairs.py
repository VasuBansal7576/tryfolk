"""Independent live readback of paired populations, source revisions and account access."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import httpx


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("after_ids", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    owner = {"Authorization": "Bearer " + keys["owner"]}
    oracle = {"Authorization": "Bearer " + keys["fixture"]}
    proof = []
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:

        async def read(url, headers):
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

        for after_id in args.after_ids:
            after = await read("http://127.0.0.1:8000/runs/" + after_id, owner)
            before = await read("http://127.0.0.1:8000/runs/" + after["parent_run_id"], owner)
            assert before["phase"] == after["phase"] == "completed"
            assert (
                before["population_id"] == after["population_id"] and before["personas"] == after["personas"]
            )
            assert before["counts"]["executed"] == after["counts"]["executed"] == after["persona_count"]
            assert (
                before["counts"]["failed"] == after["persona_count"]
                and after["counts"]["completed"] == after["persona_count"]
            )
            health = await read(after["preview_url"] + "/health", {})
            assert (
                health["revision"] == after["revision"]
                and health["source_digest"] == after["repair"]["source_digest"]
            )
            checks = []
            accounts = set()
            checkouts = set()
            for run in (before, after):
                for event in run["events"]:
                    if event["kind"] != "verification":
                        continue
                    result = event["data"]
                    payment = result["payment"]
                    account = result["entitlement"]["account_id"]
                    checkout = payment["checkout_id"]
                    assert account not in accounts and checkout not in checkouts
                    accounts.add(account)
                    checkouts.add(checkout)
                    provider = await read("http://127.0.0.1:3200/payments/" + checkout, oracle)
                    first = await read(run["preview_url"] + "/entitlements/" + account, oracle)
                    second = await read(run["preview_url"] + "/entitlements/" + account, oracle)
                    assert (
                        first == second
                        and provider["status"] == "succeeded"
                        and provider["account_id"] == account
                        and provider["payment_id"] == payment["id"]
                    )
                    assert first["granted"] == (run["pass"] == "after")
                    if first["granted"]:
                        assert (
                            first["payment_id"] == provider["payment_id"]
                            and first["units"] > 0
                            and result["capability_available"] is True
                        )
                    checks.append(
                        {
                            "pass": run["pass"],
                            "persona_id": event["persona_id"],
                            "provider": provider,
                            "entitlement": first,
                            "oracle_read_did_not_mutate": True,
                            "capability_checked": result["capability_checked"],
                            "capability_available": result["capability_available"],
                        }
                    )
            proof.append(
                {
                    "before_id": before["id"],
                    "after_id": after_id,
                    "population_id": after["population_id"],
                    "requested": after["persona_count"],
                    "population_sha256": hashlib.sha256(
                        json.dumps(after["personas"], sort_keys=True).encode()
                    ).hexdigest(),
                    "same_saved_population": True,
                    "fresh_accounts_and_checkouts": True,
                    "source_health": health,
                    "comparison": after["comparison"],
                    "budget": after["budget"],
                    "checks": checks,
                    "provider_mode": "local_fixture",
                    "genuine_dodo_payment": False,
                }
            )
    Path(args.output).write_text(json.dumps(proof, indent=2))
    print(
        json.dumps(
            {
                "verified_pairs": len(proof),
                "persona_pairs": sum(x["requested"] for x in proof),
                "output": args.output,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
