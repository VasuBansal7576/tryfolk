"""Distinct owner-approved baseline attempts; history and released estimates stay visible.

A private reviewed pre-submission approval is deliberately distinct from provider
payment evidence. Absence of an outcome is never sufficient to approve a retry.
"""

from datetime import datetime, timezone
import json
import re
import secrets

from .budget import allowance
from .scenarios import digest, unavailable, validate_population
from .service import DomainError, identifier, now


def costs_for(service, count):
    try:
        return allowance(service.settings, count)
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise DomainError(422, "cfo_export_invalid", str(error)) from error


def snapshot(service, db, run_id):
    run = service.store.load(db, "runs", run_id)
    if run.get("baseline_retry"):
        raise DomainError(409, "baseline_retry_exists", "This historical attempt already has a retry.")
    related = [
        json.loads(row[0])
        for row in db.execute("SELECT payload FROM runs")
        if json.loads(row[0])["reservation_id"] == run["reservation_id"]
    ]
    if (
        run["provider"] != "dodo"
        or run["pass"] != "before"
        or run["phase"] != "blocked"
        or run.get("retry_of_run_id")
        or run["outcomes"]
        or run.get("repair")
        or len(related) != 1
        or db.execute("SELECT 1 FROM findings WHERE run_id=?", (run_id,)).fetchone()
        or db.execute("SELECT 1 FROM repairs WHERE run_id=?", (run_id,)).fetchone()
        or db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()
    ):
        raise DomainError(
            409,
            "baseline_retry_ineligible",
            "Only a blocked original Dodo before with no results, repairs or active jobs can retry.",
        )
    population = service.store.load(db, "populations", run["population_id"])
    if (
        not isinstance(population, list)
        or len(population) != run["persona_count"]
        or len({p.get("id") for p in population}) != len(population)
        or any(
            not all(
                p.get(k)
                for k in ("id", "profile", "initial_snapshot", "initial_semantic_memory", "conditions")
            )
            for p in population
        )
    ):
        raise DomainError(
            409, "population_integrity", "Exactly N complete saved initial personas are required."
        )
    try:
        validate_population(population, run.get("scenario_plan"))
    except ValueError as error:
        raise DomainError(409, "population_integrity", str(error)) from error
    money = db.execute("SELECT * FROM reservations WHERE id=?", (run["reservation_id"],)).fetchone()
    if not money or money["released"] or money["after_started"] or money["reserved"] <= money["spent"]:
        raise DomainError(409, "baseline_retry_ineligible", "The original unspent hold must remain intact.")
    events = [
        {**dict(row), "data": json.loads(row["data"])}
        for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))
    ]
    # Positive/ambiguous verification is incompatible with the pre-submission approval.
    if any(
        e["kind"] in ("verification", "capability", "scenario_payment_condition")
        or (
            e["kind"] == "browser_action"
            and e["data"].get("action", {}).get("_tryfolk", {}).get("kind") in ("pay", "confirm")
        )
        for e in events
    ):
        raise DomainError(
            409, "baseline_retry_ineligible", "Payment-stage evidence requires independent reconciliation."
        )
    state = {
        "run_id": run_id,
        "population_id": run["population_id"],
        "population_sha256": digest(population),
        "source_commit": run.get("source_commit"),
        "source_digest": run.get("source_digest"),
        "events_sha256": digest(events),
        "historical_spent_microusd": money["spent"],
        "release_microusd": money["reserved"] - money["spent"],
    }
    receipt = run.get("baseline_retry_safety")
    if receipt:
        from .pre_submission import verified_receipt

        if not verified_receipt(receipt, state, service.settings.owner_token):
            raise DomainError(
                409,
                "baseline_retry_approval",
                "Server pre-submission receipt no longer matches this attempt.",
            )
        approval = receipt
    else:
        path = service.settings.baseline_retry_approval_path
        try:
            if not path or not path.is_absolute() or not path.is_file() or path.stat().st_mode & 0o077:
                raise ValueError(
                    "Approval must be an explicitly configured private absolute file (mode0600)."
                )
            approval = json.loads(path.read_text())
            if (
                approval.get("schema") != "tryfolk.baseline-retry-approval.v1"
                or approval.get("pre_submission_confirmed") is not True
                or not re.fullmatch(r"[a-f0-9]{64}", approval.get("evidence_sha256", ""))
                or any(approval.get(k) != v for k, v in state.items())
            ):
                raise ValueError("Reviewed pre-submission evidence must bind the exact original state.")
        except (ValueError, TypeError, OSError, AttributeError) as error:
            raise DomainError(409, "baseline_retry_approval", str(error)) from error
    target = service.target(run["app_url"])
    if (
        target.provider != "dodo"
        or target.revision != run["revision"]
        or target.source_commit != run.get("source_commit")
        or target.source_digest != run.get("source_digest")
        or target.fault != run["fault"]
        or (run.get("target_mode") is not None and target.mode != run["target_mode"])
    ):
        raise DomainError(
            409, "revision_changed", "Retry must use the exact registered original target source."
        )
    if run.get("scenario_plan") and any(
        unavailable(a["scenario_snapshot"], target)
        or a["condition_fixture_snapshot"] != target.scenario_conditions
        for a in run["scenario_plan"]["assignments"]
    ):
        raise DomainError(
            409, "scenario_support_changed", "Saved scenario conditions are no longer registered."
        )
    return (
        run,
        population,
        money,
        target,
        {**state, "approval_sha256": digest(approval), "run_sha256": digest(run)},
    )


def budget_view(service, db, money, costs):
    available = service.available(db, costs)
    release = money["reserved"] - money["spent"]
    ledger = service.ledger(db)
    after_release = max(
        0, costs["limit"] - ledger["estimated_consumed_microusd"] - ledger["held_microusd"] + release
    )
    return {
        "historical_spent_microusd": money["spent"],
        "original_reserved_microusd": money["reserved"],
        "release_microusd": release,
        "available_before_release_microusd": available,
        "available_after_release_microusd": after_release,
        "new_reserved_microusd": costs["reserved"],
        "available_after_reserve_microusd": after_release - costs["reserved"],
        "measured_cash_microusd": 0,
        "cost_basis": "estimate",
    }


def gates_for(service, target, count):
    gates = list(service.settings.gates) + list(target.setup_gates)
    if not target.execution_ready:
        gates.append(
            {
                "code": "dodo_runtime_validation",
                "message": "Dodo sandbox execution is not validated.",
                "action": "Validate the owned test target before accepting a new attempt.",
            }
        )
    if count > service.settings.max_personas:
        gates.append(
            {
                "code": "capacity",
                "message": "Saved N exceeds execution capacity.",
                "action": "Restore capacity; N cannot shrink.",
            }
        )
    if not service.settings.cfo_plan_path or not service.settings.cfo_plan_path.exists():
        gates.append(
            {
                "code": "cfo_plan_missing",
                "message": "Official CFO plan required.",
                "action": "Import owner-inspected plan.",
            }
        )
    return gates


def quote_retry(service, run_id, token):
    with service.store.transaction() as db:
        service.authorize(db, run_id, token, owner=True)
        run, population, money, target, state = snapshot(service, db, run_id)
        costs = costs_for(service, run["persona_count"])
        q = service.quote_payload(
            db,
            run["app_url"],
            run["persona_count"],
            target,
            run.get("scenario_plan"),
            gates_for(service, target, run["persona_count"]),
            costs,
        )
        retry_budget = budget_view(service, db, money, costs)
        q.update(
            kind="baseline_retry",
            retry_of_run_id=run_id,
            population_id=run["population_id"],
            population_sha256=state["population_sha256"],
            persona_ids=[p["id"] for p in population],
            retry_budget=retry_budget,
            retry_snapshot=state,
            affordable=costs["reserved"]
            <= min(costs["quote_limit"], retry_budget["available_after_release_microusd"]),
        )
        db.execute("INSERT INTO quotes(id,payload) VALUES (?,?)", (q["id"], json.dumps(q)))
        return q


def accept_retry(service, run_id, quote_id, key, token):
    with service.store.transaction() as db:
        service.authorize(db, run_id, token, owner=True)
        request = json.dumps({"run_id": run_id, "quote_id": quote_id}, sort_keys=True)
        old = db.execute(
            "SELECT request,response FROM idempotency WHERE key=?", ("baseline_retry:" + key,)
        ).fetchone()
        if old:
            if old[0] != request:
                raise DomainError(409, "idempotency_conflict", "Retry key belongs to another request.")
            return json.loads(old[1])
        q = service.store.load(db, "quotes", quote_id)
        if not q:
            raise DomainError(404, "quote_missing", "Quote not found.")
        if q.get("kind") != "baseline_retry" or q.get("retry_of_run_id") != run_id:
            raise DomainError(409, "baseline_retry_stale", "Quote is not bound to this original attempt.")
        if datetime.fromisoformat(q["expires_at"]) < datetime.now(timezone.utc):
            raise DomainError(409, "quote_expired", "Request a fresh retry quote.")
        if db.execute("SELECT consumed FROM quotes WHERE id=?", (quote_id,)).fetchone()[0]:
            raise DomainError(409, "quote_consumed", "Quote already accepted.")
        candidate = service.store.load(db, "runs", run_id)
        saved = service.store.load(db, "populations", candidate["population_id"])
        if digest(saved) != q["population_sha256"]:
            raise DomainError(409, "population_integrity", "Saved population changed since retry quote.")
        original, population, money, target, state = snapshot(service, db, run_id)
        costs = costs_for(service, original["persona_count"])
        if state != q["retry_snapshot"] or digest(costs["plan"]) != digest(q["cfo_plan"]):
            raise DomainError(
                409, "baseline_retry_stale", "Reviewed history, population or CFO plan changed."
            )
        if q["gates"] or gates_for(service, target, original["persona_count"]):
            raise DomainError(409, "setup_not_ready", "Fresh configured readiness is required.")
        if q["reserved_microusd"] > min(q["quote_limit_microusd"], costs["quote_limit"]):
            raise DomainError(402, "quote_budget", "Both new passes exceed the quote cap.")
        budget = budget_view(service, db, money, {**costs, "reserved": q["reserved_microusd"]})
        if budget["available_after_reserve_microusd"] < 0:
            raise DomainError(402, "budget", "Insufficient remaining lifetime allowance for BOTH new passes.")
        new_id, access = identifier(), secrets.token_urlsafe(32)
        lineage = {
            "original_run_id": run_id,
            "retry_run_id": new_id,
            "population_id": original["population_id"],
            "population_sha256": state["population_sha256"],
            "persona_ids": q["persona_ids"],
            "approval_sha256": state["approval_sha256"],
            "accepted_at": now(),
            "retry_budget": budget,
            "history_excluded_from_retry_denominator": True,
            "payment_safety_basis": "server_recorded_pre_submission_evidence_not_provider_receipt"
            if original.get("baseline_retry_safety")
            else "owner_reviewed_pre_submission_evidence_not_provider_receipt",
        }
        run = {
            **original,
            "id": new_id,
            "parent_run_id": None,
            "retry_of_run_id": run_id,
            "baseline_retry": lineage,
            "retry_budget": budget,
            "phase": "queued",
            "pass": "before",
            "reservation_id": new_id,
            "created_at": now(),
            "updated_at": now(),
            "gates": [],
            "budget_contract": q,
            "after_reserve_microusd": q["after_reserve_microusd"],
            "after_reserve_includes_known_replay": q["after_reserve_includes_known_replay"],
            "outcomes": {},
            "repair": None,
            "comparison": None,
            "provenance": q["provenance"],
        }
        run.pop("baseline_retry_safety", None)
        # All writes are in the same BEGIN IMMEDIATE boundary. No cancellation or historical job mutation.
        db.execute(
            "UPDATE reservations SET released=released+? WHERE id=?",
            (budget["release_microusd"], original["reservation_id"]),
        )
        db.execute("INSERT INTO reservations(id,reserved) VALUES (?,?)", (new_id, q["reserved_microusd"]))
        db.execute("INSERT INTO runs VALUES (?,?,?)", (new_id, json.dumps(run), access))
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (identifier(), new_id, "run", "queued", now()))
        original["baseline_retry"] = lineage
        service.store.save(db, "runs", original)
        for attempt in (run_id, new_id):
            db.execute(
                "INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
                (attempt, "baseline_retry_accepted", now(), json.dumps(lineage)),
            )
        db.execute("UPDATE quotes SET consumed=1 WHERE id=?", (quote_id,))
        response = {"id": new_id, "access_token": access, "status": service.view(db, run)}
        db.execute(
            "INSERT INTO idempotency VALUES (?,?,?)", ("baseline_retry:" + key, request, json.dumps(response))
        )
        return response
