from datetime import datetime, timedelta, timezone
import json
import secrets
from urllib.parse import urlsplit
from uuid import uuid4
from pathlib import Path
import os
import re

from .config import Settings
from .store import Store
from .budget import allowance
from .scenarios import (
    Catalog,
    read_data,
    read_bank,
    catalog_view,
    select_plan,
    unavailable,
    validate_population,
    coverage,
)


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return uuid4().hex


class DomainError(Exception):
    def __init__(self, status, code, message, gates=None):
        self.status = status
        self.detail = {"code": code, "message": message}
        if gates:
            self.detail["gates"] = gates


class Service:
    def __init__(self, settings: Settings, *, checkpoint_transport=None):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.checkpoint_transport = checkpoint_transport

    def target(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.fragment:
            raise DomainError(422, "unsafe_origin", "Use an explicitly registered owned test origin.")
        for target in self.settings.targets:
            if url.rstrip("/") == target.url.rstrip("/"):
                return target
        raise DomainError(422, "connection_required", "This URL has no approved owned sandbox connection.")

    def ledger(self, db):
        spent, held = db.execute(
            "SELECT coalesce(sum(spent),0),coalesce(sum(reserved-spent-released),0) FROM reservations"
        ).fetchone()
        return {"estimated_consumed_microusd": spent, "held_microusd": held}

    def available(self, db, costs=None):
        ledger = self.ledger(db)
        try:
            limit = (costs or allowance(self.settings, 0))["limit"]
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise DomainError(422, "cfo_export_invalid", str(error)) from error
        return max(0, limit - sum(ledger.values()))

    def scenarios(self, url):
        target = self.target(url)
        if not self.settings.scenario_catalog_path:
            raise DomainError(
                409,
                "scenario_catalog_missing",
                "Import the owned scenario catalog and register supported target conditions.",
            )
        try:
            return catalog_view(read_data(self.settings.scenario_catalog_path, Catalog), target)
        except (ValueError, TypeError, OSError) as error:
            raise DomainError(422, "scenario_data_invalid", str(error)) from error

    def scenario_plan(self, target, count, scenario_ids):
        if not self.settings.scenario_catalog_path:
            if scenario_ids is not None:
                raise DomainError(
                    422, "scenario_catalog_missing", "Manual scenario selection requires the owned catalog."
                )
            return None, []
        try:
            catalog = read_data(self.settings.scenario_catalog_path, Catalog)
            bank = (
                read_bank(self.settings.seed_bank_path, self.settings.seed_bank_min_records)
                if self.settings.seed_bank_path
                else None
            )
            plan = select_plan(catalog, bank, target, count, scenario_ids)
        except (ValueError, TypeError, OSError) as error:
            raise DomainError(422, "scenario_data_invalid", str(error)) from error
        gates = []
        if len(plan["assignments"]) != count:
            gates.append(
                {
                    "code": "scenario_support_missing",
                    "message": "No runnable customer scenario matches the registered target capabilities and conditions.",
                    "action": "Inspect GET /scenarios; register genuine supported fixture capabilities before a fresh quote.",
                }
            )
        if bank is None:
            gates.append(
                {
                    "code": "seed_bank_missing",
                    "message": "Licensed quality-checked persona seed bank not imported.",
                    "action": "Import the normalized licensed seed bank locally, then obtain a fresh quote.",
                }
            )
        return plan, gates

    def quote(self, url, count, scenario_ids=None):
        target = self.target(url)
        scenario_plan, scenario_gates = self.scenario_plan(target, count, scenario_ids)
        gates = list(self.settings.gates) + scenario_gates
        gates.extend(target.setup_gates)
        if count > self.settings.max_personas:
            gates.append(
                {
                    "code": "capacity",
                    "message": f"Local execution allowance is {self.settings.max_personas} personas.",
                    "action": "Choose a count within the allowance; no count will be reduced automatically.",
                }
            )
        if target.provider == "dodo":
            if not target.execution_ready:
                gates.append(
                    {
                        "code": "dodo_runtime_validation",
                        "message": "Dodo target provider/entitlement and checkout browser adapter require credential-backed validation.",
                        "action": "Complete the genuine owned Dodo target handoff and test-only adapter validation; local fixture proof does not clear this gate.",
                    }
                )
            if not self.settings.cfo_plan_path or not self.settings.cfo_plan_path.exists():
                gates.append(
                    {
                        "code": "cfo_plan_missing",
                        "message": "The official CFO business plan has not been imported.",
                        "action": "Import the owner-created official business plan with genuine Page URL/ID and inspected before/after assumptions.",
                    }
                )
        try:
            costs = allowance(self.settings, count)
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise DomainError(422, "cfo_export_invalid", str(error)) from error
        with self.store.transaction() as db:
            q = self.quote_payload(db, url, count, target, scenario_plan, gates, costs)
            db.execute("INSERT INTO quotes(id,payload) VALUES (?,?)", (q["id"], json.dumps(q)))
        return q

    def quote_payload(self, db, url, count, target, scenario_plan, gates, costs):
        before, after, overhead = costs["before"], costs["after"], costs["overhead"]
        lifetime_available = self.available(db, costs)
        available = min(lifetime_available, costs["quote_limit"])
        q = {
            "id": identifier(),
            **({"scenario_plan": scenario_plan} if scenario_plan else {}),
            "app_url": url,
            "persona_count": count,
            "max_personas": self.settings.max_personas,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "affordable": costs["reserved"] <= available,
            "ready": not gates,
            "before_microusd": before,
            "after_microusd": after,
            "overhead_microusd": overhead,
            "subtotal_microusd": costs["subtotal"],
            "contingency_microusd": costs["contingency"],
            "contingency_basis_points": costs["contingency_basis_points"],
            "after_contingency_microusd": costs["after_contingency"],
            "after_reserve_microusd": costs["after_reserve"],
            "after_reserve_includes_known_replay": costs["after_reserve_includes_known_replay"],
            "reserved_microusd": costs["reserved"],
            "quote_limit_microusd": costs["quote_limit"],
            "lifetime_limit_microusd": costs["limit"],
            "lifetime_available_microusd": lifetime_available,
            "ledger": self.ledger(db),
            "forecast_comparison": costs["forecast_comparison"],
            "operator_forecast": {
                "ceiling_microusd": self.settings.budget_microusd,
                "basis": "estimated execution allowance",
                "funded_credit_microusd": None,
                "incremental_cash_authorized": False,
                **({"allocation": costs["plan"]["operator_allocation"]}
                   if costs["plan"] and costs["plan"].get("operator_allocation") else {}),
            },
            "execution_allowances": costs["caps"],
            "available_microusd": available,
            "currency": "USD",
            "cost_basis": "estimate",
            "cfo_plan_status": "imported"
            if self.settings.cfo_plan_path and self.settings.cfo_plan_path.exists()
            else "missing",
            "gates": gates,
            "revision": target.revision,
            "fault": target.fault,
            "provider": target.provider,
            "target_provenance": target.target_provenance,
            "estimate_basis": costs["basis"],
            "cfo_plan": costs["plan"],
            "scheduler_event_limit_microusd": costs["limit"],
            "provenance": {
                "provider_mode": "local_fixture" if target.provider == "local-fixture" else "dodo_test",
                "payment_truth": "local_fixture" if target.provider == "local-fixture" else "dodo_sandbox",
                "cost_basis": "non_cfo_estimate" if not costs["plan"] else "cfo_forecast",
                "cfo_plan_status": costs["status"],
                "cfo_plan_url": costs["plan"]["source_url"] if costs["plan"] else None,
                "model": "gpt-6.1-sol",
                "model_billing": "existing_codex_allowance",
                "cash_charged_microusd": 0,
            },
        }
        return q

    def baseline_retry_quote(self, run_id, token):
        from .baseline_retry import quote_retry

        return quote_retry(self, run_id, token)

    def baseline_retry(self, run_id, quote_id, key, token):
        from .baseline_retry import accept_retry

        return accept_retry(self, run_id, quote_id, key, token)

    def create_run(self, quote_id, key):
        with self.store.transaction() as db:
            request = json.dumps({"quote_id": quote_id}, sort_keys=True)
            old = db.execute(
                "SELECT request,response FROM idempotency WHERE key=?", ("run:" + key,)
            ).fetchone()
            if old:
                if old[0] != request:
                    raise DomainError(
                        409, "idempotency_conflict", "Key was already used with a different request."
                    )
                return json.loads(old[1])
            q = self.store.load(db, "quotes", quote_id)
            if not q:
                raise DomainError(404, "quote_missing", "Quote not found.")
            if q.get("kind") == "baseline_retry":
                raise DomainError(
                    409, "owner_required", "Use the owner baseline retry endpoint for this quote."
                )
            if datetime.fromisoformat(q["expires_at"]) < datetime.now(timezone.utc):
                raise DomainError(409, "quote_expired", "Request a fresh quote.")
            if db.execute("SELECT consumed FROM quotes WHERE id=?", (quote_id,)).fetchone()[0]:
                raise DomainError(409, "quote_consumed", "Quote has already been used.")
            if q["persona_count"] > self.settings.max_personas or any(
                gate["code"] == "capacity" for gate in q["gates"]
            ):
                raise DomainError(
                    422,
                    "capacity",
                    f"Request a fresh quote within the current {self.settings.max_personas}-persona allowance. "
                    "A capacity-gated quote cannot start a job; the requested count is never reduced.",
                )
            if q["reserved_microusd"] > q.get(
                "quote_limit_microusd", q.get("scheduler_event_limit_microusd", self.settings.budget_microusd)
            ):
                raise DomainError(
                    402, "quote_budget", "Both complete passes exceed this quote's customer cap."
                )
            if self.available(db) < q["reserved_microusd"]:
                raise DomainError(402, "budget", "Insufficient budget for BOTH complete passes.")
            target = self.target(q["app_url"])
            if target.revision != q["revision"]:
                raise DomainError(
                    409, "revision_changed", "Target changed since quote; request a fresh quote."
                )
            if q.get("scenario_plan") and any(
                unavailable(a["scenario_snapshot"], target)
                or a["condition_fixture_snapshot"] != target.scenario_conditions
                for a in q["scenario_plan"]["assignments"]
            ):
                raise DomainError(
                    409,
                    "scenario_support_changed",
                    "Registered scenario capabilities or conditions changed since quote; obtain a fresh quote.",
                )
            run_id, pop_id, token = identifier(), identifier(), secrets.token_urlsafe(32)
            run = {
                "id": run_id,
                "app_url": q["app_url"],
                "persona_count": q["persona_count"],
                "population_id": pop_id,
                "parent_run_id": None,
                "pass": "before",
                "phase": "blocked" if q["gates"] else "queued",
                "revision": q["revision"],
                "fault": q["fault"],
                "preview_url": q["app_url"],
                "provider": q["provider"],
                "source_commit": target.source_commit,
                "source_digest": target.source_digest,
                "target_mode": target.mode,
                "provenance": q["provenance"],
                "target_provenance": q.get("target_provenance"),
                "created_at": now(),
                "updated_at": now(),
                "gates": q["gates"],
                "reservation_id": run_id,
                "after_reserve_microusd": q.get("after_reserve_microusd", q["after_microusd"]),
                "after_reserve_includes_known_replay": q.get("after_reserve_includes_known_replay", False),
                "budget_contract": q,
                **({"scenario_plan": q["scenario_plan"]} if q.get("scenario_plan") else {}),
                "outcomes": {},
                "repair": None,
                "comparison": None,
            }
            db.execute("INSERT INTO runs VALUES (?,?,?)", (run_id, json.dumps(run), token))
            db.execute("INSERT INTO populations VALUES (?,?)", (pop_id, "[]"))
            db.execute("INSERT INTO reservations(id,reserved) VALUES (?,?)", (run_id, q["reserved_microusd"]))
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)",
                (identifier(), run_id, "run", "blocked" if q["gates"] else "queued", now()),
            )
            db.execute("UPDATE quotes SET consumed=1 WHERE id=?", (quote_id,))
            response = {"id": run_id, "access_token": token, "status": self.view(db, run)}
            db.execute(
                "INSERT INTO idempotency VALUES (?,?,?)", ("run:" + key, request, json.dumps(response))
            )
            return response

    def authorize(self, db, run_id, token, owner=False):
        row = db.execute("SELECT token FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise DomainError(404, "run_missing", "Run not found.")
        is_owner = bool(
            self.settings.owner_token and token and secrets.compare_digest(token, self.settings.owner_token)
        )
        if owner and not is_owner:
            raise DomainError(403, "owner_required", "Only the connected owner may repair or replay.")
        if not is_owner and (not token or not secrets.compare_digest(token, row[0])):
            raise DomainError(401, "unauthorized", "A run access token is required.")

    def view(self, db, run):
        persons = self.store.load(db, "populations", run["population_id"]) or []
        outcomes = run["outcomes"]
        counts = dict.fromkeys(("completed", "failed", "abandoned", "pending", "blocked"), 0)
        for result in outcomes.values():
            counts[result["outcome"]] += 1
        n = run["persona_count"]
        if run["phase"] == "blocked":
            counts["blocked"] += n - len(outcomes)
        counts.update(
            requested=n,
            generated=len(persons),
            queued=max(0, n - len(outcomes)) if run["phase"] not in ("blocked", "cancelled") else 0,
            executed=len(outcomes),
        )
        money = db.execute("SELECT * FROM reservations WHERE id=?", (run["reservation_id"],)).fetchone()
        events = [
            {**dict(e), "data": json.loads(e["data"])}
            for e in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run["id"],))
        ]
        evidence_root = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")).resolve()
        for event in events:
            screenshot = event["data"].get("screenshot")
            if screenshot:
                source = Path(screenshot).resolve()
                event["data"]["screenshot"] = (
                    f"/runs/{run['id']}/evidence/{source.name}"
                    if source.parent == evidence_root
                    and source.name.startswith(run["id"] + "-")
                    and source.suffix == ".png"
                    and source.is_file()
                    else None
                )
        comparison = run.get("comparison")
        if comparison:
            comparison = dict(comparison)
            controls = [c for e in events if e["kind"] == "held_out_checks" for c in e["data"]["cases"]]
            comparison["held_out_checks"] = {
                "total": len(controls),
                "passed": sum(c["passed"] for c in controls),
                "failed": sum(not c["passed"] for c in controls),
                "cases": controls,
                "not_in_population_denominator": True,
            }
        return {
            **{k: v for k, v in run.items() if k not in ("reservation_id", "outcomes")},
            "comparison": comparison,
            "counts": counts,
            "budget": {
                "reserved_microusd": money["reserved"],
                "spent_microusd": money["spent"],
                "estimated_consumed_microusd": money["spent"],
                "measured_cash_microusd": 0,
                "cash_basis": "No API billing or charges; local browsers and existing Codex allowance. Token/quota use is separate.",
                "unused_microusd": money["reserved"] - money["spent"] - money["released"],
                "released_microusd": money["released"],
                "cost_basis": "estimate; measured cash tracked separately",
            },
            "personas": persons,
            **(
                {
                    "scenario_coverage": coverage(
                        run["scenario_plan"],
                        persons,
                        outcomes,
                        events,
                        run,
                        [
                            json.loads(f[0])
                            for f in db.execute("SELECT payload FROM findings WHERE run_id=?", (run["id"],))
                        ],
                        evidence_root,
                    )
                }
                if run.get("scenario_plan")
                else {}
            ),
            "events": events,
        }

    def get_run(self, run_id, token):
        with self.store.transaction() as db:
            self.authorize(db, run_id, token)
            return self.view(db, self.store.load(db, "runs", run_id))

    def cancel(self, run_id, token):
        with self.store.transaction() as db:
            self.authorize(db, run_id, token)
            run = self.store.load(db, "runs", run_id)
            # All jobs sharing this reservation must stop before releasing its remainder.
            for row in db.execute("SELECT payload FROM runs").fetchall():
                related = json.loads(row[0])
                if related["reservation_id"] == run["reservation_id"] and (
                    related["id"] == run_id or related["phase"] not in ("completed", "cancelled")
                ):
                    related.update(phase="cancelled", updated_at=now())
                    self.store.save(db, "runs", related)
                    db.execute(
                        "UPDATE jobs SET status='cancelled',updated_at=? WHERE run_id=?",
                        (now(), related["id"]),
                    )
                    if related["id"] == run_id:
                        run = related
            db.execute("UPDATE reservations SET released=reserved-spent WHERE id=?", (run["reservation_id"],))
            return self.view(db, run)

    def findings(self, run_id, token):
        with self.store.transaction() as db:
            self.authorize(db, run_id, token)
            return {
                "findings": [
                    json.loads(row[0])
                    for row in db.execute("SELECT payload FROM findings WHERE run_id=?", (run_id,))
                ]
            }

    @staticmethod
    def remaining_after_reserve(run):
        protected = run["after_reserve_microusd"]
        if run.get("after_reserve_includes_known_replay") and run.get("known_replay_allowance_consumed"):
            protected -= run["budget_contract"]["execution_allowances"]["known_replay_microusd"]
        return protected

    def recover_connection(self, run_id, token):
        with self.store.transaction() as db:
            self.authorize(db, run_id, token, owner=True)
            run = self.store.load(db, "runs", run_id)
            if run.get("recovery") and run["phase"] == "queued":
                return self.view(db, run)
            action_events = db.execute(
                "SELECT count(*) FROM events WHERE run_id=? AND kind IN ('persona_initialized','observation','browser_action','verification')",
                (run_id,),
            ).fetchone()[0]
            diagnostics = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT data FROM events WHERE run_id=? AND kind='known_failure_replay'", (run_id,)
                )
            ]
            diagnostic_only = bool(diagnostics) and all(
                event.get("diagnostic") is True
                and event.get("outcome") == "blocked"
                and event.get("classification") == "verification_unavailable"
                and event.get("payment") is None
                for event in diagnostics
            )
            recoveries = list(run.get("recoveries", [run["recovery"]] if run.get("recovery") else []))
            connection_only = any(g["message"] == "All connection attempts failed" for g in run["gates"])
            money = db.execute("SELECT * FROM reservations WHERE id=?", (run["reservation_id"],)).fetchone()
            if (
                run["provider"] != "local-fixture"
                or run["pass"] != "after"
                or run["phase"] != "blocked"
                or run["outcomes"]
                or action_events
                or not (connection_only or diagnostic_only)
                or len(recoveries) >= 2
                or money["released"]
                or money["reserved"] - money["spent"] < self.remaining_after_reserve(run)
            ):
                raise DomainError(
                    409,
                    "recovery_not_safe",
                    "Recovery requires no adaptive work, no observed payment, the full after reserve, and fewer than two prior bounded fixture recoveries.",
                )
            recovery = {
                "kind": "connection_only_preview" if connection_only else "unpaid_fixture_diagnostic",
                "diagnostic_allowance_already_consumed_microusd": run.get("budget_contract", {})
                .get("execution_allowances", {})
                .get("known_replay_microusd", 10_000),
                "additional_estimated_consumption_microusd": 0,
                "measured_cash_microusd": 0,
                "basis": "At most three local diagnostic attempts use the original quoted diagnostic allowance. Trusted recorded actions require no new model inference or cash payment. Historical consumption is retained; the full N-persona after pass and its quoted contingency remain reserved.",
                "prior_gates": run["gates"],
                "requested_at": now(),
            }
            run.update(
                phase="queued",
                gates=[],
                recovery=recovery,
                recoveries=[*recoveries, recovery],
                known_replay_allowance_consumed=True,
                updated_at=now(),
            )
            self.store.save(db, "runs", run)
            db.execute(
                "UPDATE jobs SET status='queued',updated_at=? WHERE run_id=? AND kind='run'", (now(), run_id)
            )
            db.execute(
                "INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
                (run_id, "connection_recovery_requested", now(), json.dumps(recovery)),
            )
            return self.view(db, run)

    def repair(self, run_id, finding_id, token):
        with self.store.transaction() as db:
            self.authorize(db, run_id, token, owner=True)
            run = self.store.load(db, "runs", run_id)
            finding = self.store.load(db, "findings", finding_id)
            if not finding or finding["run_id"] != run_id or run["phase"] != "completed":
                raise DomainError(
                    409,
                    "verified_finding_required",
                    "A completed run and its independently correlated defect are required.",
                )
            if not finding["measured"] and finding["evidence"].get("provider") != "local-fixture":
                raise DomainError(
                    409,
                    "fixture_disclosure",
                    "Only measured findings or disclosed owned local-fixture defects can be repaired.",
                )
            target = self.target(run["app_url"])
            if not target.repo_path:
                raise DomainError(409, "owned_source_required", "No owned source workspace registered.")
            if run["repair"]:
                return run["repair"]
            repair = {
                "id": identifier(),
                "run_id": run_id,
                "finding_id": finding_id,
                "status": "queued",
                "base_revision": run["revision"],
                "head_revision": None,
                "preview_url": None,
                "checkpoint_id": None,
                "checkpoint_url": None,
                "gates": [],
            }
            run["repair"] = repair
            self.store.save(db, "runs", run)
            db.execute("INSERT INTO repairs VALUES (?,?,?)", (repair["id"], run_id, json.dumps(repair)))
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)", (identifier(), run_id, "repair", "queued", now())
            )
            return repair

    def recover_repair(self, run_id, token):
        """Owner-requested reconciliation only; never repeat code or browser side effects."""
        with self.store.transaction() as db:
            self.authorize(db, run_id, token, owner=True)
            run = self.store.load(db, "runs", run_id)
            repair = run.get("repair")
            if run["phase"] == "cancelled":
                raise DomainError(409, "cancelled", "Cancelled runs cannot recover a repair.")
            if repair and repair["status"] in ("completed", "queued", "running"):
                return repair
            money = db.execute("SELECT * FROM reservations WHERE id=?", (run["reservation_id"],)).fetchone()
            if (
                not repair
                or repair["status"] != "blocked"
                or run["provider"] not in ("local-fixture", "dodo")
                or run["pass"] != "before"
                or run["phase"] != "completed"
                or len(repair.get("recoveries", [])) >= 2
                or money["released"]
                or money["reserved"] - money["spent"]
                < run["after_reserve_microusd"]
                + (0 if run.get("after_reserve_includes_known_replay") else 10_000)
            ):
                raise DomainError(
                    409,
                    "repair_recovery_not_safe",
                    "Recovery requires a blocked saved local repair, its completed baseline, and the full diagnostic and after reserve.",
                )
            recovery = {
                "requested_at": now(),
                "mode": "verify_existing_checkpoint_only",
                "additional_estimated_consumption_microusd": 0,
                "previous_gates": repair["gates"],
            }
            repair.update(status="queued", recoveries=[*repair.get("recoveries", []), recovery])
            run["repair"] = repair
            self.store.save(db, "repairs", repair)
            self.store.save(db, "runs", run)
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)",
                (identifier(), run_id, "repair_recovery", "queued", now()),
            )
            db.execute(
                "INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
                (run_id, "repair_capture_recovery_requested", now(), json.dumps(recovery)),
            )
            return repair

    def finalize_checkpoint_access(self, run_id, repair_id, token):
        from .checkpoint_access import (
            CheckpointAccessError,
            load_capture_binding,
            verify_public_access,
            private_write,
            encoded,
            digest,
        )

        def registered(db):
            self.authorize(db, run_id, token, owner=True)
            run = self.store.load(db, "runs", run_id)
            repair = self.store.load(db, "repairs", repair_id)
            if (
                not repair
                or repair["run_id"] != run_id
                or (run.get("repair") or {}).get("id") != repair_id
                or run["phase"] != "completed"
                or repair["status"] != "completed"
                or run["provider"] != "dodo"
                or repair.get("provider") != "dodo"
            ):
                raise DomainError(
                    409, "checkpoint_not_registered", "A completed genuine captured Dodo repair is required."
                )
            return run, repair

        with self.store.transaction() as db:
            run, repair = registered(db)
        try:
            binding, binding_digest, repository = load_capture_binding(self.settings, run, repair)
            existing = repair.get("checkpoint_access")
            if (
                existing
                and existing["binding_sha256"] == binding_digest
                and existing["repository"] == repository
            ):
                return repair
            access = verify_public_access(
                binding, binding_digest, repository, transport=self.checkpoint_transport
            )
        except CheckpointAccessError as error:
            raise DomainError(
                409,
                str(error),
                "Public checkpoint access is unverified; inspect the bound capture and approved repository.",
            ) from None
        access["verified_at"] = now()
        receipt = encoded(access)
        access["receipt_sha256"] = digest(receipt)
        with self.store.transaction() as db:
            current_run, current_repair = registered(db)
            if any(
                current_repair.get(key) != repair.get(key)
                for key in ("head_revision", "checkpoint_id", "source_digest")
            ):
                raise DomainError(
                    409, "checkpoint_capture_changed", "Registered source changed during access verification."
                )
            if current_repair.get("checkpoint_access") == existing:
                try:
                    private_write(
                        self.settings.checkpoint_evidence_root
                        / (repair_id + "-checkpoint-public-access-" + access["receipt_sha256"] + ".json"),
                        receipt,
                    )
                except (CheckpointAccessError, OSError):
                    raise DomainError(
                        409,
                        "checkpoint_evidence_unavailable",
                        "Private access evidence could not be retained.",
                    ) from None
                current_repair.update(checkpoint_url=access["url"], checkpoint_access=access)
                self.store.save(db, "repairs", current_repair)
                current_run["repair"] = current_repair
                self.store.save(db, "runs", current_run)
            return current_repair

    def register_repair(
        self,
        repair_id,
        *,
        head_revision,
        preview_url,
        checkpoint_id,
        checkpoint_url,
        provider,
        source_digest=None,
        target_revision=None,
    ):
        # Worker-only registration boundary; no client-selected preview URL endpoint exists.
        parsed = urlsplit(preview_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port not in (range(3310, 3400) if provider == "dodo" else range(3102, 3200))
        ):
            raise DomainError(422, "unsafe_preview", "Preview must use a registered owned loopback port.")
        with self.store.transaction() as db:
            repair = self.store.load(db, "repairs", repair_id)
            if not repair:
                raise DomainError(404, "repair_missing", "Repair not found.")
            run = self.store.load(db, "runs", repair["run_id"])
            if provider != run["provider"] or (
                provider == "dodo"
                and (
                    not isinstance(source_digest, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", source_digest)
                    or not isinstance(head_revision, str)
                    or not re.fullmatch(r"[a-f0-9]{40}", head_revision)
                    or not isinstance(target_revision, str)
                    or not re.fullmatch(r"sha256:[a-f0-9]{64}", target_revision)
                )
            ):
                raise DomainError(
                    422,
                    "preview_provenance",
                    "Dodo source commit, digest, target revision and provider must match the owned run.",
                )
            if run["phase"] == "cancelled":
                raise DomainError(409, "cancelled", "Cancelled runs cannot publish a repair preview.")
            repair.update(
                status="completed",
                head_revision=head_revision,
                preview_url=preview_url,
                checkpoint_id=checkpoint_id,
                checkpoint_url=checkpoint_url,
                provider=provider,
                source_digest=source_digest,
                target_revision=target_revision,
                resolved_gates=repair.get("gates", []),
                gates=[],
            )
            self.store.save(db, "repairs", repair)
            run["repair"] = repair
            self.store.save(db, "runs", run)
            db.execute(
                "UPDATE jobs SET status='completed' WHERE run_id=? AND kind IN ('repair','repair_recovery') AND status IN ('queued','running')",
                (run["id"],),
            )

    def replay(self, run_id, repair_id, key, token):
        with self.store.transaction() as db:
            self.authorize(db, run_id, token, owner=True)
            request = json.dumps({"run_id": run_id, "repair_id": repair_id}, sort_keys=True)
            old = db.execute(
                "SELECT request,response FROM idempotency WHERE key=?", ("replay:" + key,)
            ).fetchone()
            if old:
                if old[0] != request:
                    raise DomainError(409, "idempotency_conflict", "Replay key used for a different request.")
                return json.loads(old[1])
            before = self.store.load(db, "runs", run_id)
            repair = self.store.load(db, "repairs", repair_id)
            population = self.store.load(db, "populations", before["population_id"])
            money = db.execute(
                "SELECT * FROM reservations WHERE id=?", (before["reservation_id"],)
            ).fetchone()
            if (
                before["pass"] != "before"
                or before["phase"] != "completed"
                or not repair
                or repair["run_id"] != run_id
                or repair["status"] != "completed"
            ):
                raise DomainError(
                    409, "repair_not_ready", "Replay requires this run's completed repaired revision."
                )
            if len(population) != before["persona_count"] or money["after_started"] or money["released"]:
                raise DomainError(
                    409,
                    "population_or_reservation",
                    "Exactly the original N and its unused retest reservation are required.",
                )
            try:
                validate_population(population, before.get("scenario_plan"))
            except ValueError as error:
                raise DomainError(409, "population_integrity", str(error)) from error
            after_id, access = identifier(), secrets.token_urlsafe(32)
            after = {
                **before,
                "id": after_id,
                "parent_run_id": run_id,
                "pass": "after",
                "phase": "queued",
                "app_url": repair["preview_url"],
                "preview_url": repair["preview_url"],
                "revision": repair.get("target_revision") or repair["head_revision"],
                "source_commit": repair["head_revision"],
                "source_digest": repair.get("source_digest"),
                "fault": "repaired",
                "outcomes": {},
                "created_at": now(),
                "updated_at": now(),
                "gates": [],
                "comparison": None,
            }
            db.execute("INSERT INTO runs VALUES (?,?,?)", (after_id, json.dumps(after), access))
            db.execute("UPDATE reservations SET after_started=1 WHERE id=?", (before["reservation_id"],))
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?)", (identifier(), after_id, "run", "queued", now())
            )
            response = {"id": after_id, "access_token": access, "status": self.view(db, after)}
            db.execute(
                "INSERT INTO idempotency VALUES (?,?,?)", ("replay:" + key, request, json.dumps(response))
            )
            return response
