"""Owner-reviewed fresh after populations with retained real diagnostic proof."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from .baseline_retry import budget_view, costs_for, gates_for
from .budget import contingency
from .scenarios import digest, validate_population
from .service import DomainError, identifier, now


def require(value, message):
    if not value:
        raise DomainError(409, "after_retry_ineligible", message)


def diagnostic_proof(db, run):
    events = [{**dict(row), "data": json.loads(row["data"])} for row in db.execute(
        "SELECT * FROM events WHERE run_id=? ORDER BY id", (run["id"],))]
    complete = [e for e in events if e["kind"] == "known_failure_replay"
                and e["data"].get("outcome") == "completed"]
    require(len(complete) == 1, "One actual completed known-case diagnostic is required.")
    outcome = complete[0]["data"]
    require(outcome.get("provider") == "dodo" and outcome.get("diagnostic") is True
            and outcome.get("not_in_population_denominator") is True
            and outcome.get("revision") == run["revision"]
            and all(outcome.get(k) is True for k in ("measured", "payment_verified",
                    "entitlement_verified", "capability_checked", "capability_available")),
            "Known-case payment, grant and download must genuinely complete on this revision.")
    capability = [e for e in events if e["kind"] == "known_replay_capability"
                  and e["id"] < complete[0]["id"]]
    controls = [e for e in events if e["kind"] == "held_out_checks"
                and e["id"] > complete[0]["id"]
                and {c.get("case") for c in e["data"].get("cases", [])}
                == {"duplicate_reconciliation_grants_once", "unpaid_access_denied"}]
    require(bool(capability) and len(controls) == 1
            and all(c.get("passed") is True for c in controls[0]["data"]["cases"]),
            "The completed diagnostic requires its actual download and both held-out checks.")
    selected = [capability[-1], complete[0], controls[0]]
    trace_urls = {e["trace_url"] for e in selected}
    require(len(trace_urls) == 1 and None not in trace_urls, "Diagnostic spans must belong to one real trace.")
    trace_id = urlsplit(next(iter(trace_urls))).path.rsplit("/", 1)[-1]
    evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
    receipt_path = evidence / f"{run['id']}-{trace_id}-trace.json"
    raw_path = evidence / f"{run['id']}-{trace_id}-readback.json"
    require(all(p.is_file() and not p.is_symlink() for p in (receipt_path, raw_path)),
            "Finalized hosted diagnostic receipt must be retained.")
    receipt, raw = (json.loads(p.read_text()) for p in (receipt_path, raw_path))
    spans = [s for s in raw.get("spans", []) if s.get("node_name") != "neatlogs.trace.complete"]
    require(receipt.get("run_id") == run["id"] and receipt.get("trace_id") == trace_id
            and receipt.get("delivery_flushed") is True and receipt.get("finalized_readback") == "verified"
            and raw.get("_id") == trace_id and raw.get("status") in {"success", "error"}
            and len({s["span_id"] for s in spans}) == len(spans)
            and {s["span_id"]: s.get("node_name") for s in spans} == receipt.get("expected_spans"),
            "Hosted diagnostic trace must contain every original finalized span.")
    event_bindings = []
    for event in selected:
        payload_hash = hashlib.sha256(json.dumps(event["data"], default=str).encode()).hexdigest()
        matches = []
        for span in spans:
            output = (span.get("data") or {}).get("output")
            output = json.loads(output) if isinstance(output, str) else output
            if (span.get("node_name") == "tryfolk." + event["kind"] and isinstance(output, dict)
                    and output.get("observed_payload_sha256") == payload_hash):
                matches.append(span)
        require(len(matches) == 1, "Hosted diagnostic payload hash must match its original event.")
        event_bindings.append({"event_id": event["id"], "kind": event["kind"],
                               "recorded_at": event["created_at"], "payload_sha256": payload_hash,
                               "span_id": matches[0]["span_id"]})
    download = capability[-1]["data"].get("result", {}).get("capability", {})
    path = Path(download.get("download_path", ""))
    require(download.get("available") is True and download.get("source") == "stagehand_browser_download"
            and path.is_file() and not path.is_symlink() and path.stat().st_size == download.get("bytes")
            and hashlib.sha256(path.read_bytes()).hexdigest() == download.get("download_sha256"),
            "The diagnostic's actual protected download bytes must remain intact.")
    # Keep stable receipt facts. Later legitimate read attempts may append receipt metadata.
    return {"source_run_id": run["id"], "trace_id": trace_id,
            "trace_url": next(iter(trace_urls)), "events": event_bindings,
            "download_path": str(path), "download_sha256": download["download_sha256"],
            "download_bytes": download["bytes"], "target_revision": run["revision"],
            "source_digest": run["source_digest"], "head_revision": run["source_commit"],
            "held_out_cases": controls[0]["data"]["cases"],
            "held_out_recorded_at": controls[0]["created_at"],
            "completed_diagnostic_recorded_at": complete[0]["created_at"]}


def retained_proof(service, db, run):
    """Follow retained owner-approved lineage to the original real diagnostic."""
    current, seen, chain = run, set(), []
    while current.get("after_retry_of_run_id"):
        require(current["id"] not in seen and len(seen) < 32, "Retry lineage is cyclic or unbounded.")
        seen.add(current["id"])
        chain.append(current)
        parent = service.store.load(db, "runs", current["after_retry_of_run_id"])
        lineage = current.get("after_retry") or {}
        require(parent and parent.get("after_retry_child_run_id") == current["id"]
                and lineage.get("original_run_id") == parent["id"]
                and lineage.get("retry_run_id") == current["id"]
                and lineage.get("parent_before_run_id") == current["parent_run_id"]
                and all(current.get(k) == parent.get(k) for k in (
                    "parent_run_id", "population_id", "persona_count", "revision", "source_digest",
                    "source_commit", "preview_url")), "The retained retry chain must preserve its exact source and cohort.")
        current = parent
    proof = diagnostic_proof(db, current)
    require(all(item.get("known_replay_reused") == proof
                and item["after_retry"].get("known_replay_source_run_id") == proof["source_run_id"]
                for item in chain),
        "The retry must retain the original genuinely completed diagnostic.")
    return proof


def snapshot(service, db, run_id):
    old = service.store.load(db, "runs", run_id)
    before = service.store.load(db, "runs", old.get("parent_run_id"))
    repair = service.store.load(db, "repairs", (old.get("repair") or {}).get("id"))
    require(old["pass"] == "after" and old["provider"] == "dodo" and old["phase"] == "blocked"
            and not old.get("after_retry_child_run_id") and not old.get("outcomes")
            and before and before["pass"] == "before" and before["phase"] == "completed"
            and repair and repair.get("status") == "completed" and repair.get("provider") == "dodo"
            and not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone(),
            "One blocked after pass with zero outcomes and its completed before/repair are required with no active jobs.")
    pins = ("id", "run_id", "head_revision", "preview_url", "checkpoint_id", "source_digest", "target_revision")
    require(all((old.get("repair") or {}).get(k) == repair.get(k)
                and (before.get("repair") or {}).get(k) == repair.get(k) for k in pins)
            and old["preview_url"] == repair["preview_url"] and old["revision"] == repair["target_revision"]
            and old["source_digest"] == repair["source_digest"] and old["source_commit"] == repair["head_revision"],
            "The retry must retain the exact registered repair source.")
    population = service.store.load(db, "populations", old["population_id"])
    require(old["population_id"] == before["population_id"]
            and old["persona_count"] == before["persona_count"] == len(population or [])
            and len({p.get("id") for p in population}) == len(population)
            and all(isinstance(p.get("profile"), dict) and p.get("initial_snapshot")
                    and isinstance(p.get("initial_semantic_memory"), (dict, list))
                    and isinstance(p.get("conditions"), dict)
                    for p in population), "Exactly the complete original saved cohort is required.")
    service.validate_count(old["persona_count"])
    validate_population(population, old.get("scenario_plan"))
    money = db.execute("SELECT * FROM reservations WHERE id=?", (old["reservation_id"],)).fetchone()
    require(money and money["after_started"] and not money["released"], "The historical remaining hold must be intact.")
    proof = retained_proof(service, db, old)
    target = replace(service.target(before["app_url"]), url=repair["preview_url"], revision=repair["target_revision"],
                     source_commit=repair["head_revision"], source_digest=repair["source_digest"],
                     fault=old["fault"], mode=old.get("target_mode") or service.target(before["app_url"]).mode)
    state = {"run_sha256": digest(old), "population_sha256": digest(population), "repair_sha256": digest({k: repair.get(k) for k in pins}),
             "events_sha256": digest([dict(e) for e in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run_id,))]),
             "money": dict(money), "known_replay_proof": proof}
    return old, before, repair, population, money, target, state


def after_costs(service, before, count):
    costs = costs_for(service, count)
    saved = before["budget_contract"]
    ignored = {"operator_allocation"}
    if (costs.get("plan") or {}).get("operator_allocation") and not (saved.get("cfo_plan") or {}).get("operator_allocation"):
        # Importing the separately validated addition generates a derivative snapshot hash.
        ignored.add("snapshot_sha256")
    current_native = {k: v for k, v in (costs.get("plan") or {}).items() if k not in ignored}
    saved_native = {k: v for k, v in (saved.get("cfo_plan") or {}).items() if k not in ignored}
    require(costs.get("plan") and digest(current_native) == digest(saved_native)
            and (not saved.get("cfo_plan", {}).get("operator_allocation")
                 or costs["plan"].get("operator_allocation") == saved["cfo_plan"]["operator_allocation"])
            and costs["caps"] == saved["execution_allowances"], "The original inspected CFO plan and quoted rates must remain unchanged.")
    base = count * saved["execution_allowances"]["after_per_persona_microusd"]
    extra = contingency(base, saved["contingency_basis_points"])
    return {**costs, "before": 0, "after": base, "overhead": 0, "subtotal": base,
            "contingency": extra, "after_contingency": extra, "after_reserve": base + extra,
            "after_reserve_includes_known_replay": False, "reserved": base + extra,
            "forecast_comparison": None}


def quote_retry(service, run_id, token):
    with service.store.transaction() as db:
        service.authorize(db, run_id, token, owner=True)
        old, before, repair, population, money, target, state = snapshot(service, db, run_id)
        costs = after_costs(service, before, old["persona_count"])
        q = service.quote_payload(db, target.url, old["persona_count"], target, old.get("scenario_plan"),
                                  gates_for(service, target, old["persona_count"]), costs)
        budget = budget_view(service, db, money, costs)
        q.update(kind="after_retry", retry_of_run_id=run_id, before_run_id=before["id"],
                 population_id=old["population_id"], population_sha256=state["population_sha256"],
                 persona_ids=[p["id"] for p in population], repair_id=repair["id"],
                 head_revision=repair["head_revision"], target_revision=repair["target_revision"],
                 source_digest=repair["source_digest"], retry_budget=budget, retry_snapshot=state,
                 known_replay_reused=state["known_replay_proof"],
                 known_replay_source_run_id=state["known_replay_proof"]["source_run_id"],
                 affordable=costs["reserved"] <= min(costs["quote_limit"], budget["available_after_release_microusd"]))
        db.execute("INSERT INTO quotes(id,payload) VALUES (?,?)", (q["id"], json.dumps(q)))
        return q


def accept_retry(service, run_id, quote_id, key, token):
    with service.store.transaction() as db:
        service.authorize(db, run_id, token, owner=True)
        request = json.dumps({"run_id": run_id, "quote_id": quote_id}, sort_keys=True)
        previous = db.execute("SELECT request,response FROM idempotency WHERE key=?", ("after_retry:" + key,)).fetchone()
        if previous:
            require(previous[0] == request, "Idempotency key belongs to another retry request.")
            return json.loads(previous[1])
        q = service.store.load(db, "quotes", quote_id)
        require(q and q.get("kind") == "after_retry" and q.get("retry_of_run_id") == run_id,
                "An owner-reviewed after retry quote is required.")
        require(datetime.fromisoformat(q["expires_at"]) >= datetime.now(timezone.utc)
                and not db.execute("SELECT consumed FROM quotes WHERE id=?", (quote_id,)).fetchone()[0], "Quote expired or consumed.")
        old, before, repair, population, money, target, state = snapshot(service, db, run_id)
        costs = after_costs(service, before, old["persona_count"])
        require(state == q["retry_snapshot"]
                and q.get("known_replay_source_run_id") == state["known_replay_proof"]["source_run_id"]
                and digest(costs["plan"]) == digest(q["cfo_plan"])
                and costs["reserved"] == q["reserved_microusd"], "Reviewed cohort, history, source, diagnostic or forecast changed.")
        require(not q["gates"] and not gates_for(service, target, old["persona_count"]), "Current execution readiness is required.")
        budget = budget_view(service, db, money, costs)
        if costs["reserved"] > costs["quote_limit"] or budget["available_after_reserve_microusd"] < 0:
            raise DomainError(402, "budget", "The complete saved after cohort exceeds remaining forecast allowance.")
        new_id, access = identifier(), secrets.token_urlsafe(32)
        lineage = {"original_run_id": run_id, "retry_run_id": new_id, "parent_before_run_id": before["id"],
                   "population_id": old["population_id"], "population_sha256": state["population_sha256"],
                   "persona_ids": q["persona_ids"], "repair_id": repair["id"], "head_revision": repair["head_revision"],
                   "target_revision": repair["target_revision"], "source_digest": repair["source_digest"],
                   "retry_budget": budget, "known_replay_source_run_id": state["known_replay_proof"]["source_run_id"], "accepted_at": now(),
                   "history_excluded_from_retry_denominator": True}
        fresh = {**old, "id": new_id, "after_retry_of_run_id": run_id, "after_retry": lineage,
                 "reservation_id": new_id, "phase": "queued", "outcomes": {}, "gates": [], "comparison": None,
                 "budget_contract": q, "after_reserve_microusd": costs["reserved"],
                 "after_reserve_includes_known_replay": False, "known_replay_reused": state["known_replay_proof"],
                 "created_at": now(), "updated_at": now()}
        for field in ("recovery", "recoveries", "execution_attempt", "known_replay_allowance_consumed", "after_retry_child_run_id"):
            fresh.pop(field, None)
        db.execute("UPDATE reservations SET released=released+? WHERE id=?", (budget["release_microusd"], old["reservation_id"]))
        db.execute("INSERT INTO reservations(id,reserved,after_started) VALUES (?,?,1)", (new_id, costs["reserved"]))
        db.execute("INSERT INTO runs VALUES (?,?,?)", (new_id, json.dumps(fresh), access))
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (identifier(), new_id, "run", "queued", now()))
        old["after_retry_child_run_id"] = new_id
        service.store.save(db, "runs", old)
        for attempt in (run_id, new_id):
            db.execute("INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
                       (attempt, "after_retry_accepted", now(), json.dumps(lineage)))
        db.execute("UPDATE quotes SET consumed=1 WHERE id=?", (quote_id,))
        response = {"id": new_id, "access_token": access, "status": service.view(db, fresh)}
        db.execute("INSERT INTO idempotency VALUES (?,?,?)", ("after_retry:" + key, request, json.dumps(response)))
        return response


def validate_reuse(service, db, run, population):
    """Recheck actual retained proof before the worker releases any new customer."""
    old = service.store.load(db, "runs", run.get("after_retry_of_run_id"))
    before = service.store.load(db, "runs", run.get("parent_run_id"))
    repair = service.store.load(db, "repairs", (run.get("repair") or {}).get("id"))
    lineage = run.get("after_retry", {})
    require(old and before and repair and old.get("after_retry_child_run_id") == run["id"]
            and lineage.get("original_run_id") == old["id"] and lineage.get("retry_run_id") == run["id"]
            and lineage.get("parent_before_run_id") == before["id"]
            and run["population_id"] == old["population_id"] == before["population_id"]
            and digest(population) == lineage.get("population_sha256")
            and run["revision"] == old["revision"] == repair.get("target_revision")
            and run["source_digest"] == old["source_digest"] == repair.get("source_digest")
            and run["source_commit"] == old["source_commit"] == repair.get("head_revision")
            and run["preview_url"] == old["preview_url"] == repair.get("preview_url")
            and lineage.get("known_replay_source_run_id") == run["known_replay_reused"].get("source_run_id")
            and run["known_replay_reused"] == retained_proof(service, db, old),
            "The retained diagnostic and exact cohort/source lineage must remain intact.")
    return run["known_replay_reused"]
