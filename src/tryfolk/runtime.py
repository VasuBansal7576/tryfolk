"""Real SDK orchestration and independent owned-target verification."""

import asyncio
import json
import os
import signal
import time
from pathlib import Path
import httpx
from .codex_bridge import clean_env
from .process import SDKProcess, PersonaPool, BrowserLease, ROOT
from .verification import Payment, Entitlement, verify
from .worker import Cancelled
from .browser_policy import BrowserPolicy
from .decisions import validated_action

NATIVE_PAY_START_SPACING_SECONDS = 0
HOSTED_CHECKOUT_QUEUE_TIMEOUT_SECONDS = 300


class FreshActionRetry:
    """Only proved nonexecuted field changes permit a new observed selection."""

    def __init__(self, *, timeout=180):
        self.deadline = time.monotonic() + timeout
        self.discarded = 0
        self.manual_binding = None

    def remaining(self):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise RuntimeError("Original fresh action deadline exhausted; no action dispatched.")
        return left

    def allow(self, error, action, document=None):
        data = getattr(error, "sdk_data", None)
        metadata = action.get("_tryfolk") or {}
        expected = {"same_url": True, "same_generation": True,
                    "same_control_signature": False, "nonfinancial": True, "executed": False}
        if (not isinstance(data, dict) or any(data.get(k) is not value for k, value in expected.items())
                or self.discarded >= 3 or time.monotonic() >= self.deadline):
            return False
        if getattr(error, "sdk_code", None) == "nonfinancial_manual_control_stale":
            if (set(data) != {*expected, "manual_variant"} or data["manual_variant"] != "enter_manually"
                    or not isinstance(document, dict) or metadata.get("url") != document.get("url")
                    or not isinstance(document.get("generation"), (int, float))
                    or isinstance(document.get("generation"), bool)
                    or not 0 < document["generation"] < float("inf")
                    or action.get("method") != "click" or action.get("arguments") != []
                    or metadata.get("diagnostic_manual") is not True
                    or metadata.get("manual_variant") != "enter_manually"
                    or metadata.get("kind") != "click" or metadata.get("field") or metadata.get("frame_url")):
                return False
            binding = (document["url"], document["generation"], "enter_manually")
            if self.manual_binding is not None and binding != self.manual_binding:
                return False
            self.manual_binding = binding
        elif (getattr(error, "sdk_code", None) != "nonfinancial_control_stale"
                or set(data) != {*expected, "field"}
                or data["field"] not in ("address", "city", "state", "postal")
                or data["field"] != metadata.get("field") or metadata.get("frame_url")
                or metadata.get("kind") in ("pay", "confirm") or action.get("method") != "fill"):
            return False
        self.discarded += 1
        return True

    def accepts_replacement(self, action, document):
        if self.manual_binding is None:
            return True
        metadata = action.get("_tryfolk") or {}
        return bool(isinstance(document, dict)
                    and (document.get("url"), document.get("generation"), metadata.get("manual_variant")) == self.manual_binding
                    and metadata.get("url") == document.get("url")
                    and metadata.get("diagnostic_manual") is True and metadata.get("kind") == "click"
                    and not metadata.get("field") and not metadata.get("frame_url")
                    and action.get("method") == "click" and action.get("arguments") == [])


class Runtime:
    def __init__(self, service):
        self.service = service
        self.evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")).resolve()
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.secret = os.environ.get("TARGET_FIXTURE_SECRET", "")
        self.headers = {"Authorization": "Bearer " + self.secret}
        from .telemetry import Telemetry

        self.telemetry = Telemetry()
        settings = getattr(service, "settings", None)
        self.bulk_execution = bool(getattr(settings, "bulk_execution", False))
        self.journey_concurrency = getattr(settings, "browser_concurrency", 2) if self.bulk_execution else 1
        self.bulk_minds = {}
        self.browser_pools = {}
        self.browser_hosts = {}
        self.native_pay_permits = {}
        self.hosted_checkout_permits = {}
        self.native_pay_started = {}
        self.native_pay_clock = time.monotonic

    def bulk_mind(self, run_id):
        if run_id not in self.bulk_minds:
            self.bulk_minds[run_id] = PersonaPool(
                self.journey_concurrency, run_id, lambda: self.cancelled(run_id), evidence_root=self.evidence,
            )
        return self.bulk_minds[run_id]

    async def lease_browser(self, run_id, cancelled, frame_verify):
        if run_id not in self.browser_pools:
            pool = asyncio.Queue()
            hosts = [SDKProcess("stagehand", cancelled, reuse_browser=True, evidence_root=self.evidence)
                     for _ in range(self.journey_concurrency)]
            for host in hosts:
                pool.put_nowait(host)
            self.browser_pools[run_id], self.browser_hosts[run_id] = pool, hosts
        pool = self.browser_pools[run_id]
        host = await pool.get()
        host.frame_verify = frame_verify
        def release(closed_host, reusable):
            if reusable:
                pool.put_nowait(closed_host)
            else:
                # A fresh SDK object has no stale process, context or callback.
                replacement = SDKProcess("stagehand", cancelled, reuse_browser=True, evidence_root=self.evidence)
                self.browser_hosts[run_id].append(replacement)
                pool.put_nowait(replacement)

        return BrowserLease(host, release)

    async def close_run(self, run_id):
        for mapping in (self.native_pay_permits, self.hosted_checkout_permits, self.native_pay_started):
            for key in tuple(mapping):
                if key == run_id or (isinstance(key, tuple) and key[0] == run_id):
                    mapping.pop(key, None)
        mind = self.bulk_minds.pop(run_id, None)
        if mind:
            await mind.close()
        hosts = self.browser_hosts.pop(run_id, [])
        self.browser_pools.pop(run_id, None)
        await asyncio.gather(*(host.close() for host in hosts))

    def cancelled(self, run_id):
        with self.service.store.transaction() as db:
            return self.service.store.load(db, "runs", run_id)["phase"] == "cancelled"

    async def check_revision(self, run):
        if run["provider"] == "dodo":
            from .dodo import DodoTarget

            manifest = (await DodoTarget(run["preview_url"]).registration())["manifest"]
            if manifest["target_revision"] != run["revision"]:
                raise RuntimeError("Dodo preview revision changed since registration.")
            if any(
                run.get(key) and run[key] != manifest[field]
                for key, field in (("source_commit", "source_commit"), ("source_digest", "source_sha256"))
            ):
                raise RuntimeError("Dodo preview source head/digest differs from this exact run.")
            if run.get("target_mode") and manifest["mode"] != run["target_mode"]:
                raise RuntimeError("Dodo baseline mode changed; repair must keep equivalent conditions.")
            return manifest
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            response = await client.get(run["preview_url"] + "/health")
            response.raise_for_status()
            health = response.json()
            if health["revision"] != run["revision"] or health["fault"] != run["fault"]:
                raise RuntimeError(
                    "Registered preview revision/fault changed; evidence acceptance invalidated."
                )
            if health["provider"] != run["provider"]:
                raise RuntimeError("Preview provider identity mismatch.")
            expected_digest = (
                run.get("source_digest") or (run.get("repair") or {}).get("source_digest")
                if run["pass"] == "after"
                else run["revision"]
            )
            if health.get("source_digest") != expected_digest:
                raise RuntimeError("Preview source digest differs from the registered exact revision.")

    async def discover(self, run):
        self.active_run_id = run["id"]
        await self.check_revision(run)
        env = clean_env() | {
            "PYTHONPATH": str(ROOT / "src"),
            "ANONYMIZED_TELEMETRY": "false",
            "BROWSER_USE_TELEMETRY": "false",
            "TRYFOLK_EVIDENCE_ROOT": str(self.evidence.resolve()),
        }
        process = await asyncio.create_subprocess_exec(
            str(ROOT / "integrations/browser-use/.venv/bin/python"),
            str(ROOT / "integrations/browser-use/adapter.py"),
            cwd=ROOT / "runtime",
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        task = asyncio.create_task(
            process.communicate(
                json.dumps(
                    {"url": run["app_url"], "revision": run["revision"], "provider": run["provider"]}
                ).encode()
            )
        )
        deadline = asyncio.get_running_loop().time() + 180
        try:
            while not task.done():
                if self.cancelled(run["id"]):
                    raise Cancelled()
                if asyncio.get_running_loop().time() > deadline:
                    raise RuntimeError("Discovery time allowance exhausted.")
                await asyncio.sleep(0.25)
            stdout, _ = await task
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
        (self.evidence / f"{run['id']}-discovery.log").write_bytes(stdout)
        if process.returncode:
            raise RuntimeError("Browser Use discovery failed; see discovery evidence log.")
        for line in reversed(stdout.decode().splitlines()):
            try:
                value = json.loads(line)
                if "observed" in value:
                    value["provider"] = run["provider"]
                    return value
            except ValueError:
                continue
        raise RuntimeError("Browser Use produced no observed AppBrief.")

    async def generate(self, brief, count):
        if self.bulk_execution and brief.get("provider") == "dodo" and brief.get("generation_plan"):
            # Generation has no active journey memory and must not occupy one of
            # the independently leased decision lanes.
            mind = SDKProcess(
                "tinytroupe", lambda: self.cancelled(self.active_run_id), evidence_root=self.evidence,
            )
            population = []
            try:
                index, capacity = 0, 64
                while index < count:
                    chunk_count = min(capacity, count - index)
                    try:
                        value = await mind.request(
                            {"op": "generate_bulk", "brief": brief, "count": chunk_count,
                             "start_index": index, "run_id": self.active_run_id}, timeout=600,
                        )
                    except RuntimeError as error:
                        if (getattr(error, "sdk_code", None) == "bulk_input_bound"
                            and getattr(error, "model_invoked", None) is False and chunk_count > 1):
                            capacity = max(1, chunk_count // 2)
                            continue  # No model/browser/payment call happened; split exact inputs.
                        raise
                    chunk = value["population"]
                    if len(chunk) != chunk_count or [p["index"] for p in chunk] != list(range(index, index + chunk_count)):
                        raise RuntimeError("Bulk generation must retain every exact assigned persona index; N cannot shrink.")
                    population.extend(chunk)
                    index += chunk_count
                    if len({p["id"] for p in population}) != len(population):
                        raise RuntimeError("Bulk generation returned duplicate persona IDs.")
                    # Persist actual generation progress. Requested N and accepted
                    # plan stay immutable; partial state is never a completed population.
                    with self.service.store.transaction() as db:
                        run = self.service.store.load(db, "runs", self.active_run_id)
                        if run["phase"] == "cancelled":
                            raise Cancelled()
                        db.execute("UPDATE populations SET payload=? WHERE id=?",
                                   (json.dumps(population), run["population_id"]))
                return population
            finally:
                await mind.close()
        persona = SDKProcess("tinytroupe", lambda: self.cancelled(self.active_run_id), evidence_root=self.evidence)
        try:
            value = await persona.request(
                {"op": "generate", "brief": brief, "count": count}, timeout=180 * count
            )
            population = value["population"]
            if self.bulk_execution and brief.get("provider") == "dodo":
                from copy import deepcopy
                from .bulk_personas import validated_purchase_plan

                original = deepcopy(population)
                value = await persona.request(
                    {"op": "prepare_intents", "brief": brief, "population": population},
                    timeout=max(180, 60 * count),
                )
                population = value["population"]
                if len(population) != count or len(original) != count:
                    raise RuntimeError("Intention preparation cannot reduce the requested population.")
                for before, prepared in zip(original, population, strict=True):
                    if any(prepared.get(key) != before.get(key) for key in
                           ("id", "index", "name", "profile", "conditions")):
                        raise RuntimeError("Intention preparation must preserve each exact generated customer.")
                    memory = before.get("initial_semantic_memory")
                    prepared_memory = prepared.get("initial_semantic_memory")
                    if (not isinstance(memory, list) or not isinstance(prepared_memory, list)
                            or prepared_memory[:len(memory)] != memory
                            or not prepared.get("initial_snapshot")):
                        raise RuntimeError("Intention preparation must retain original memory in a real initial snapshot.")
                    validated_purchase_plan(prepared)
            return population
        finally:
            await persona.close()

    async def provision(self, run, persona):
        manifest = await self.check_revision(run)
        if run["provider"] == "dodo":
            from .dodo import DodoTarget

            fixture = await DodoTarget(run["preview_url"]).provision(run, persona)
            fixture["_retry_target_manifest"] = manifest
            if persona.get("assigned_scenario"):
                fixture["_scenario_source_manifest"] = {
                    key: manifest[key]
                    for key in (
                        "target_revision",
                        "source_commit",
                        "source_sha256",
                        "app_origin",
                        "provider_environment",
                        "source_clean",
                        "mode",
                    )
                }
            return fixture
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            response = await client.post(
                run["preview_url"] + "/fixtures/accounts",
                json={"run_id": run["id"], "persona_id": persona["id"], "conditions": persona["conditions"]},
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def verification(self, run, fixture, capability=None):
        if run["provider"] == "dodo":
            from .dodo import DodoTarget

            return await DodoTarget(run["preview_url"]).verification(run, fixture, capability)
        # Provider truth is read from a distinct process/database. Entitlement GET cannot fulfill.
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            response = await client.get(
                "http://127.0.0.1:3200/payments/" + fixture["checkout_id"], headers=self.headers
            )
            if response.status_code == 404:
                payment = None
            else:
                response.raise_for_status()
                value = response.json()
                payment = Payment(
                    id=value["payment_id"],
                    provider=value["provider"],
                    status=value["status"],
                    account_id=value["account_id"],
                    checkout_id=value["checkout_id"],
                    product_id=value["product_id"],
                    amount=value["amount"],
                    currency=value["currency"],
                    evidence_url="http://127.0.0.1:3200/payments/" + fixture["checkout_id"],
                )
            response = await client.get(
                run["preview_url"] + "/entitlements/" + fixture["account_id"], headers=self.headers
            )
            response.raise_for_status()
            entitlement = Entitlement.model_validate(response.json())
        return verify(
            payment,
            entitlement,
            **{k: fixture[k] for k in ("account_id", "checkout_id", "product_id", "amount", "currency")},
            settled=True,
            capability=capability,
        )

    async def journey(self, run, persona, cancelled, emit):
        from .payment_privacy import redact_payment

        original_emit = emit

        async def emit(kind, data, persona_id):
            await original_emit(kind, redact_payment(data), persona_id)

        fixture = await self.provision(run, persona)

        async def frame_verify(context):
            from .dodo import DodoTarget

            return await DodoTarget(run["preview_url"]).frame_context(context, run, fixture)

        bulk = self.bulk_execution and run["provider"] == "dodo" and bool(persona.get("purchase_plan"))
        if bulk:
            from .bulk_personas import validated_purchase_plan

            plan = validated_purchase_plan(persona)
            mind = await self.bulk_mind(run["id"]).acquire(persona["id"])
            browser = await self.lease_browser(run["id"], cancelled, frame_verify)
        else:
            browser = SDKProcess(
                "stagehand", cancelled,
                frame_verify=frame_verify if run["provider"] == "dodo" else None, evidence_root=self.evidence,
            )
            mind = SDKProcess("tinytroupe", cancelled, evidence_root=self.evidence)
        actions_executed = []
        fresh_retry = None
        capability = None
        policy = BrowserPolicy(run["preview_url"], run["provider"])
        mind_initialized = False
        scenario_binding = None
        condition_emitted = False
        pay_permit = None
        hosted_permit = None
        hosted_started = False

        async def release_hosted_permit(result):
            nonlocal hosted_permit
            lease, hosted_permit = hosted_permit, None
            await self.release_hosted_checkout(lease, result, emit, persona["id"])

        async def release_pay_permit(result):
            nonlocal pay_permit
            lease, pay_permit = pay_permit, None
            await self.release_native_admission(lease, result, emit, persona["id"])

        try:
            # Separate SDK processes can initialize together. No observation or
            # decision proceeds until the complete saved memory and browser are ready.
            startup = [
                asyncio.create_task(mind.request({"op": "init", "persona": persona})),
                asyncio.create_task(
                    browser.request(
                        {
                            "op": "goto",
                            "url": fixture["login_url"]
                            if run["provider"] == "dodo"
                            else run["preview_url"] + fixture["session_url"],
                            "origin": run["preview_url"],
                            "provider": run["provider"],
                            "card_capability": run["provider"] == "dodo",
                        }
                    )
                ),
            ]
            try:
                initial_memory, browser_started = await asyncio.gather(*startup)
            finally:
                for task in startup:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*startup, return_exceptions=True)
                mind_initialized = not startup[0].cancelled() and startup[0].exception() is None
            await emit("persona_initialized", initial_memory, persona["id"])
            if run["provider"] == "dodo" and persona.get("assigned_scenario"):
                from .scenario_evidence import binding
                from .dodo import OWNERSHIP

                scenario_binding = binding(run, persona, fixture)
                await emit(
                    "scenario_fixture",
                    {
                        "binding": scenario_binding,
                        "source_manifest": fixture.get("_scenario_source_manifest"),
                        "account": {
                            "source": "dodo_owned_fixture_provision",
                            "history": "new",
                            "ownership": {key: fixture[key] for key in OWNERSHIP},
                        },
                        "browser": browser_started.get("condition_observation"),
                    },
                    persona["id"],
                )
            if bulk:
                self.telemetry.bind_purchase_persona(persona)
                await emit("conditional_purchase_plan", {
                    "plan": plan,
                    "execution": "disclosed deterministic Stagehand controls; unexpected state uses TinyPerson",
                    "payment_outcome_inferred": False,
                }, persona["id"])
            for step in range(plan["max_steps"] if bulk else (24 if run["provider"] == "dodo" else 6)):
                if cancelled():
                    raise Cancelled()
                await self.check_revision(run)
                if fresh_retry is None:
                    fresh_retry = FreshActionRetry()
                if (run["provider"] == "dodo" and pay_permit is None
                        and not any(a.get("_tryfolk", {}).get("kind") == "pay" for a in actions_executed)
                        and {"card_number", "card_expiry", "card_cvc"}.issubset({
                            a.get("_tryfolk", {}).get("field") for a in actions_executed
                        })):
                    # Shared native Continue/Pay admission remains separate from
                    # the hosted resource queue and its fresh action deadline.
                    # Continue/Pay admission waits before fresh observe/select.
                    pay_permit = await self.acquire_native_admission(run["id"], "pay", fresh_retry, emit, persona["id"])
                    if cancelled():
                        raise Cancelled()
                observation = await browser.request({"op": "observe_native" if bulk else "observe"},
                    timeout=fresh_retry.remaining())
                if (run["provider"] == "dodo" and hosted_permit is None
                        and any(a.get("_tryfolk", {}).get("kind") == "purchase" for a in observation["actions"])):
                    # Own only the hosted navigation-to-return window. All saved
                    # minds/browsers initialize on the owned app concurrently.
                    # No selection or stale control crosses the permit wait.
                    hosted_permit = await self.acquire_hosted_checkout(run["id"], emit, persona["id"])
                    if cancelled():
                        raise Cancelled()
                    # Resource admission carried no selected action. Start the
                    # unchanged action deadline only now, then observe afresh.
                    fresh_retry = FreshActionRetry()
                    observation = await browser.request({"op": "observe_native" if bulk else "observe"}, timeout=fresh_retry.remaining())
                if (run["provider"] == "dodo" and pay_permit is None
                        and any(a.get("_tryfolk", {}).get("kind") == "billing_continue" for a in observation["actions"])):
                    # This first observation discovers a boundary, not a selected
                    # action. Discard it, wait, then observe and decide afresh.
                    pay_permit = await self.acquire_native_admission(run["id"], "billing_continue", fresh_retry, emit, persona["id"])
                    if cancelled():
                        raise Cancelled()
                    observation = await browser.request({"op": "observe_native" if bulk else "observe"}, timeout=fresh_retry.remaining())
                policy.check_url(observation["url"])
                observed_actions = observation.pop("actions")
                actions = [
                    {"id": f"{step}:{index}", "description": a["description"]}
                    for index, a in enumerate(observed_actions)
                ]
                observation["actions"] = actions
                shot = self.evidence / f"{run['id']}-{persona['id']}-{step}.png"
                await browser.request({"op": "screenshot", "path": str(shot.resolve())},
                    timeout=fresh_retry.remaining())
                await emit("observation", {**observation, "screenshot": str(shot)}, persona["id"])
                if bulk:
                    from .bulk_personas import choose_planned_action

                    planned = choose_planned_action(plan, observed_actions, actions_executed)
                    if not plan["purchase"]:
                        decision = {"intent": {"type": "ABANDON", "target": "", "content": plan["rationale"]},
                                    "source": "saved_persona_conditional_plan"}
                    elif planned is not None:
                        selected = observed_actions.index(planned)
                        decision = {"intent": {"type": "BROWSER", "target": actions[selected]["id"],
                                               "content": plan["rationale"]},
                                    "source": "disclosed_deterministic_guarded_plan_execution"}
                    elif plan["unexpected_state"] == "abandon":
                        decision = {"intent": {"type": "ABANDON", "target": "", "content": "Unexpected current state under saved plan."},
                                    "source": "saved_persona_conditional_plan"}
                    else:
                        # The actual TinyPerson is fed its fresh state and complete
                        # recorded results. No model output can supply tool selectors/values.
                        decision = await mind.request({"op": "decide", "observation": redact_payment(observation)})
                        decision["source"] = "actual_tinyperson_unexpected_state_decision"
                else:
                    decision = await mind.request({"op": "decide", "observation": redact_payment(observation)})
                if bulk:
                    await mind.request({"op": "feedback", "result": {
                        "actual_observation": redact_payment(observation), "actual_decision": decision,
                    }})
                await emit("decision", decision, persona["id"])
                intent = decision["intent"]
                if intent["type"] in ("FINISH", "ABANDON"):
                    outcome = await self.verification(run, fixture, capability)
                    if intent["type"] == "ABANDON" and outcome["outcome"] != "failed":
                        outcome.update(outcome="abandoned", classification="persona_abandoned")
                    return {
                        **outcome,
                        "account_id": fixture["account_id"],
                        "actions": actions_executed,
                        "revision": run["revision"],
                        "conditions": persona["conditions"],
                    }
                action = validated_action(
                    intent,
                    [{"id": choice["id"], "action": observed_actions[i]} for i, choice in enumerate(actions)],
                    policy=policy,
                )
                policy.validate(action, observation["url"])
                if not fresh_retry.accepts_replacement(action, observation.get("document")):
                    raise RuntimeError("Fresh inner manual choice must match the original document; no action executed.")
                boundary_kind = action.get("_tryfolk", {}).get("kind")
                if run["provider"] == "dodo" and boundary_kind == "purchase" and hosted_permit is None:
                    raise RuntimeError("Hosted checkout requires admission before a fresh observation; no action executed.")
                if hosted_permit is not None and not hosted_started and boundary_kind != "purchase":
                    await release_hosted_permit("unused")
                is_native_boundary = run["provider"] == "dodo" and boundary_kind in ("pay", "billing_continue")
                if is_native_boundary and (pay_permit is None or pay_permit["boundary_kind"] != boundary_kind):
                    raise RuntimeError("Native boundary requires admission before a fresh observation; no action executed.")
                if pay_permit is not None and not is_native_boundary:
                    await release_pay_permit("unused")
                settled = False
                try:
                    if run["provider"] == "dodo" and boundary_kind == "purchase":
                        hosted_started = True
                    if is_native_boundary:
                        self.native_pay_started[(run["id"], persona["id"])] = self.native_pay_clock()
                    result = await browser.request({"op": "act", "action": action}, timeout=fresh_retry.remaining())
                    settled = (result.get("settlement", {}).get("url", "").split("?", 1)[0] == policy.origin + "/checkout/return"
                               if boundary_kind == "pay" else result.get("success", result.get("data", {}).get("success", True)) is True)
                except RuntimeError as error:
                    if run["provider"] == "dodo" and fresh_retry.allow(error, action, observation.get("document")):
                        continue
                    raise
                finally:
                    if is_native_boundary:
                        interrupted = cancelled() or asyncio.current_task().cancelling() > 0
                        await release_pay_permit("cancelled" if interrupted else
                            ("owned_return_settled" if boundary_kind == "pay" else "billing_controls_settled") if settled else "failed")
                        if boundary_kind == "pay" and settled:
                            await release_hosted_permit("owned_return_settled")
                fresh_retry = None
                if not result.get("success", result.get("data", {}).get("success", True)):
                    raise RuntimeError("Stagehand reported browser action failure; no payment assumption.")
                actions_executed.append(redact_payment(action))
                await emit("browser_action", {"action": action, "result": result}, persona["id"])
                if scenario_binding and not condition_emitted and result.get("condition_observation"):
                    await emit(
                        "scenario_payment_condition",
                        {
                            "binding": scenario_binding,
                            "observation": result["condition_observation"],
                        },
                        persona["id"],
                    )
                    condition_emitted = True
                await mind.request({"op": "feedback", "result": redact_payment(result)})
                if action.get("_tryfolk", {}).get("capability"):
                    capability = result.get("capability", {}).get("available")
                    await emit("capability", result.get("capability", {"available": None}), persona["id"])
                    # Capability evidence is distinct from the provider receipt and
                    # account grant. It cannot create or infer payment success.
                    return {
                        **await self.verification(run, fixture, capability),
                        "account_id": fixture["account_id"],
                        "actions": actions_executed,
                        "revision": run["revision"],
                        "conditions": persona["conditions"],
                        "capability_evidence": result.get("capability"),
                    }
                # Customer decides next step from fresh UI; provider truth is never fed to mind.
                if "capability" in action["description"].lower():
                    fresh = await browser.request({"op": "observe"})
                    shot = self.evidence / f"{run['id']}-{persona['id']}-{step}-capability.png"
                    await browser.request({"op": "screenshot", "path": str(shot.resolve())})
                    await emit(
                        "observation",
                        {**fresh, "screenshot": str(shot), "capability_observation": True},
                        persona["id"],
                    )
                    capability = "Purchased capability available" in fresh["text"]
                    return {
                        **await self.verification(run, fixture, capability),
                        "account_id": fixture["account_id"],
                        "actions": actions_executed,
                        "revision": run["revision"],
                        "conditions": persona["conditions"],
                        "capability_mutation": "Consumes one credit if access available.",
                    }
            return {
                **await self.verification(run, fixture, capability),
                "account_id": fixture["account_id"],
                "actions": actions_executed,
                "step_limit": True,
            }
        except Exception as error:
            from .pre_submission import runtime_gate

            gate = runtime_gate(error, run, persona, fixture, self.evidence)
            if gate:
                raise gate from error
            raise
        finally:
            try:
                interrupted = cancelled() or asyncio.current_task().cancelling() > 0
                await release_pay_permit("cancelled" if interrupted else "unused")
                await release_hosted_permit("cancelled" if interrupted else "failed" if hosted_started else "unused")
                if mind_initialized and not cancelled() and mind.process and mind.process.returncode is None:
                    snapshot = await mind.request({"op": "snapshot"})
                    await emit("persona_memory", snapshot, persona["id"])
            finally:
                try:
                    interrupted = cancelled() or asyncio.current_task().cancelling() > 0
                    await release_hosted_permit("cancelled" if interrupted else "failed" if hosted_started else "unused")
                finally:
                    try:
                        await browser.close()
                    finally:
                        await mind.close()

    async def acquire_hosted_checkout(self, run_id, emit, persona_id):
        permit = self.hosted_checkout_permits.setdefault((run_id, persona_id), asyncio.Lock())
        waiting_at = time.monotonic()
        common = {"capacity": 1, "ownership_scope": "persona", "queue_bound_ms": int(HOSTED_CHECKOUT_QUEUE_TIMEOUT_SECONDS * 1000),
                  "payment_outcome_inferred": False}
        await emit("hosted_checkout_admission", {**common, "phase": "wait"}, persona_id)
        await asyncio.wait_for(permit.acquire(), HOSTED_CHECKOUT_QUEUE_TIMEOUT_SECONDS)
        lease = {"permit": permit, "entered_at": time.monotonic()}
        try:
            await emit("hosted_checkout_admission", {**common, "phase": "enter",
                       "wait_ms": int((time.monotonic() - waiting_at) * 1000)}, persona_id)
            return lease
        except BaseException:
            await self.release_hosted_checkout(lease, "cancelled" if asyncio.current_task().cancelling() else "failed", emit, persona_id)
            raise

    async def release_hosted_checkout(self, lease, result, emit, persona_id):
        if lease is None or lease.get("released"):
            return
        lease["released"] = True
        lease["permit"].release()
        await emit("hosted_checkout_admission", {"phase": "exit", "result": result, "capacity": 1, "ownership_scope": "persona",
                   "queue_bound_ms": int(HOSTED_CHECKOUT_QUEUE_TIMEOUT_SECONDS * 1000),
                   "duration_ms": int((time.monotonic() - lease["entered_at"]) * 1000),
                   "payment_outcome_inferred": False}, persona_id)

    async def repair(self, run, emit):
        from .repair import repair_target

        await repair_target(self, run, emit)

    async def recover_repair(self, run, emit):
        from .repair import recover_target

        await recover_target(self, run, emit)

    async def acquire_native_admission(self, run_id, boundary_kind, retry, emit, persona_id):
        if boundary_kind not in ("pay", "billing_continue"):
            raise ValueError("Unsupported native admission boundary.")
        permit = self.native_pay_permits.setdefault((run_id, persona_id), asyncio.Lock())
        waiting_at = time.monotonic()
        common = {"capacity": 1, "ownership_scope": "persona", "boundary_kind": boundary_kind,
                  "configured_minimum_spacing_ms": int(NATIVE_PAY_START_SPACING_SECONDS * 1000),
                  "spacing_basis": "independent_persona_worker", "payment_outcome_inferred": False}
        await emit("native_pay_admission", {**common, "phase": "wait"}, persona_id)
        await asyncio.wait_for(permit.acquire(), retry.remaining())
        lease = {"permit": permit, "entered_at": time.monotonic(), "boundary_kind": boundary_kind}
        try:
            previous = self.native_pay_started.get((run_id, persona_id))
            delay = max(0, previous + NATIVE_PAY_START_SPACING_SECONDS - self.native_pay_clock()) if previous is not None else 0
            cooldown_at = time.monotonic()
            if delay:
                await asyncio.wait_for(asyncio.sleep(delay), retry.remaining())
            lease["entered_at"] = time.monotonic()
            await emit("native_pay_admission", {**common, "phase": "enter",
                       "wait_ms": int((time.monotonic() - waiting_at) * 1000),
                       "cooldown_ms": int((time.monotonic() - cooldown_at) * 1000)}, persona_id)
            return lease
        except BaseException:
            await self.release_native_admission(lease, "cancelled" if asyncio.current_task().cancelling() else "failed", emit, persona_id)
            raise

    async def release_native_admission(self, lease, result, emit, persona_id):
        if lease is None or lease.get("released"):
            return
        lease["released"] = True
        lease["permit"].release()
        await emit("native_pay_admission", {"phase": "exit", "result": result, "capacity": 1, "ownership_scope": "persona",
                   "boundary_kind": lease["boundary_kind"],
                   "duration_ms": int((time.monotonic() - lease["entered_at"]) * 1000),
                   "payment_outcome_inferred": False}, persona_id)

    async def known_replay(self, run, population, emit):
        if run["provider"] == "dodo":
            from .dodo_replay import replay

            return await replay(self, run, population, emit)
        from .repair import replay_known_failure
        from .controls import check

        await replay_known_failure(self, run, population, emit)
        controls = await check(self, run)
        await emit(
            "held_out_checks",
            {
                "cases": controls,
                "method": "deterministic fixture HTTP controls, no persona cognition or Dodo transaction",
                "not_in_population_denominator": True,
            },
        )
        if not all(c["passed"] for c in controls):
            raise RuntimeError("Separate held-out fixture control failed; adaptive retest stopped.")
