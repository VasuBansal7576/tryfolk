"""Bounded owned-fixture HTTP controls, outside the persona denominator."""

import httpx


async def check(runtime, run):
    results = []
    conditions = {"history": "new", "device": "desktop", "locale": "en-US"}
    for case in ["unpaid_access_denied", "duplicate_payment_grants_once", "healthy_revision_control"]:
        candidate = dict(run)
        if case == "healthy_revision_control":
            target = next(t for t in runtime.service.settings.targets if t.fault == "healthy")
            candidate.update(
                app_url=target.url,
                preview_url=target.url,
                revision=target.revision,
                fault=target.fault,
                pass_="before",
            )
            candidate["pass"] = "before"
        p = {"id": "heldout-" + case, "conditions": conditions}
        seed = await runtime.provision(candidate, p)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
            await client.get(candidate["preview_url"] + seed["session_url"])
            if case == "unpaid_access_denied":
                response = await client.post(candidate["preview_url"] + "/capability")
                entitlement = await client.get(
                    candidate["preview_url"] + "/entitlements/" + seed["account_id"], headers=runtime.headers
                )
                payment = await client.get(
                    "http://127.0.0.1:3200/payments/" + seed["checkout_id"], headers=runtime.headers
                )
                passed = (
                    response.status_code == 403
                    and not entitlement.json()["granted"]
                    and payment.status_code == 404
                )
                data = {
                    "capability_http": response.status_code,
                    "entitlement": entitlement.json(),
                    "provider_http": payment.status_code,
                }
            else:
                await client.post(candidate["preview_url"] + "/checkout")
                first = await client.get(
                    candidate["preview_url"] + "/entitlements/" + seed["account_id"], headers=runtime.headers
                )
                await client.post(candidate["preview_url"] + "/checkout")
                second = await client.get(
                    candidate["preview_url"] + "/entitlements/" + seed["account_id"], headers=runtime.headers
                )
                readonly = await client.get(
                    candidate["preview_url"] + "/entitlements/" + seed["account_id"], headers=runtime.headers
                )
                verification = await runtime.verification(candidate, seed)
                passed = (
                    first.json() == second.json() == readonly.json()
                    and second.json()["units"] == 10
                    and verification["outcome"] == "completed"
                )
                data = {
                    "first": first.json(),
                    "second": second.json(),
                    "readonly": readonly.json(),
                    "verification": verification,
                }
            results.append(
                {
                    "case": case,
                    "passed": passed,
                    "revision": candidate["revision"],
                    "preview_url": candidate["preview_url"],
                    "account_id": seed["account_id"],
                    "provider": "local-fixture",
                    "measured": False,
                    "not_in_population_denominator": True,
                    "evidence": data,
                }
            )
    return results
