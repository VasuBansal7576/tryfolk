"""Sequential durable jobs; bounded N is separate from browser concurrency (one)."""

import asyncio
import json
from contextlib import nullcontext
from .service import now, identifier


class Cancelled(Exception):
    pass


class Worker:
    def __init__(self, service, io):
        self.service = service
        self.store = service.store
        self.io = io

    def cancelled(self, run_id):
        with self.store.transaction() as db:
            run = self.store.load(db, "runs", run_id)
            return run["phase"] == "cancelled"

    def phase(self, run_id, phase):
        with self.store.transaction() as db:
            run = self.store.load(db, "runs", run_id)
            if run["phase"] == "cancelled":
                raise Cancelled()
            run.update(phase=phase, updated_at=now())
            self.store.save(db, "runs", run)

    def estimate(self, run, category, fallback):
        # Issued prices and phase estimates stay fixed across plan/configuration changes.
        return run.get("budget_contract", {}).get("execution_allowances", {}).get(category, fallback)

    def debit(self, run_id, estimate):
        with self.store.transaction() as db:
            run = self.store.load(db, "runs", run_id)
            if run["phase"] == "cancelled":
                raise Cancelled()
            money = db.execute("SELECT * FROM reservations WHERE id=?", (run["reservation_id"],)).fetchone()
            # Reserve the entire after population even if the baseline is expensive.
            after_floor = run["after_reserve_microusd"] if run["pass"] == "before" else 0
            if money["spent"] + estimate > money["reserved"] - money["released"] - after_floor:
                raise RuntimeError("Both-pass budget exhausted; retest reserve protected.")
            db.execute("UPDATE reservations SET spent=spent+? WHERE id=?", (estimate, run["reservation_id"]))

    async def tick(self):
        with self.store.transaction() as db:
            if db.execute("SELECT 1 FROM jobs WHERE status='running' LIMIT 1").fetchone():
                return False
            job = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY updated_at LIMIT 1"
            ).fetchone()
            if not job:
                return False
            db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now(), job["id"]))
            run = self.store.load(db, "runs", job["run_id"])
        run_id = run["id"]

        async def emit(kind, data, persona_id=None, trace_url=None):
            if self.cancelled(run_id):
                raise Cancelled()
            telemetry = getattr(self.io, "telemetry", None)
            if telemetry:
                telemetry.event(kind, data, persona_id)
                trace_url = trace_url or telemetry.url
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO events(run_id,persona_id,kind,created_at,data,trace_url) VALUES (?,?,?,?,?,?)",
                    (run_id, persona_id, kind, now(), json.dumps(data), trace_url),
                )

        try:
            telemetry = getattr(self.io, "telemetry", None)
            with telemetry.run(run) if telemetry else nullcontext():
                if job["kind"] == "repair":
                    self.debit(run_id, self.estimate(run, "repair_microusd", 20_000))
                    await self.io.repair(run, emit)
                elif job["kind"] == "repair_recovery":
                    await self.io.recover_repair(run, emit)
                else:
                    await self.execute(run, emit)
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET status='completed',updated_at=? WHERE id=? AND status!='cancelled'",
                    (now(), job["id"]),
                )
        except Cancelled:
            pass
        except Exception as error:
            # Persist a actionable error; never translate tooling errors into abandonment.
            with self.store.transaction() as db:
                run = self.store.load(db, "runs", run_id)
                if run["phase"] != "cancelled":
                    gate = {
                        "code": "runtime",
                        "message": str(error)[:300],
                        "action": "Inspect runtime evidence and resolve adapter/setup failure before retrying.",
                    }
                    if job["kind"] in ("repair", "repair_recovery"):
                        run["repair"]["status"] = "blocked"
                        run["repair"]["gates"].append(gate)
                        self.store.save(db, "repairs", run["repair"])
                    else:
                        run.update(phase="blocked", updated_at=now())
                        run["gates"].append(gate)
                        from .pre_submission import seal_failure

                        receipt = seal_failure(self.service, db, run, error)
                        if receipt:
                            run["baseline_retry_safety"] = receipt
                    self.store.save(db, "runs", run)
                    db.execute("UPDATE jobs SET status='blocked',updated_at=? WHERE id=?", (now(), job["id"]))
        return True

    async def execute(self, run, emit):
        run_id = run["id"]
        if run["pass"] == "before" and run.get("retry_of_run_id"):
            from .scenarios import digest, validate_population

            with self.store.transaction() as db:
                population = self.store.load(db, "populations", run["population_id"])
                original = self.store.load(db, "runs", run["retry_of_run_id"])
            lineage = run.get("baseline_retry", {})
            if (
                not original
                or original.get("baseline_retry") != lineage
                or lineage.get("retry_run_id") != run_id
                or original["population_id"] != run["population_id"]
                or len(population or []) != run["persona_count"]
                or digest(population) != lineage.get("population_sha256")
                or run.get("scenario_plan") != original.get("scenario_plan")
            ):
                raise RuntimeError(
                    "Retry requires the exact immutable original population and attempt lineage."
                )
            validate_population(population, run.get("scenario_plan"))
            # A fresh journey restores each saved initial snapshot. No discovery,
            # generation, population overwrite or known-replay debit occurs here.
            await emit(
                "baseline_population_reused",
                {
                    "original_run_id": original["id"],
                    "population_id": run["population_id"],
                    "population_sha256": lineage["population_sha256"],
                    "requested": run["persona_count"],
                    "initial_state": "saved_initial_snapshot",
                    "generation_performed": False,
                },
            )
        elif run["pass"] == "before":
            self.phase(run_id, "discovering")
            self.debit(run_id, self.estimate(run, "discovery_microusd", 10_000))
            brief = await self.io.discover(run)
            await emit("discovery", brief)
            self.phase(run_id, "generating")
            self.debit(run_id, self.estimate(run, "generation_microusd", 10_000))
            if run.get("scenario_plan"):
                brief = {**brief, "generation_plan": run["scenario_plan"]}
            population = await self.io.generate(brief, run["persona_count"])
            if len(population) != run["persona_count"] or len({p["id"] for p in population}) != len(
                population
            ):
                raise RuntimeError("Generation must supply exactly the requested N distinct saved personas.")
            from .scenarios import freeze_population

            population = freeze_population(population, run.get("scenario_plan"))
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE populations SET payload=? WHERE id=?",
                    (json.dumps(population), run["population_id"]),
                )
        else:
            with self.store.transaction() as db:
                population = self.store.load(db, "populations", run["population_id"])
            if len(population) != run["persona_count"]:
                raise RuntimeError("Saved population incomplete; replay cannot shrink N.")
            from .scenarios import validate_population

            validate_population(population, run.get("scenario_plan"))
            if hasattr(self.io, "known_replay"):
                if not run.get("known_replay_allowance_consumed"):
                    self.debit(run_id, self.estimate(run, "known_replay_microusd", 10_000))
                    with self.store.transaction() as db:
                        current = self.store.load(db, "runs", run_id)
                        current["known_replay_allowance_consumed"] = True
                        self.store.save(db, "runs", current)
                else:
                    await emit("connection_recovery", run["recovery"])
                await self.io.known_replay(run, population, emit)
        self.phase(run_id, "running")
        for persona in population:
            if self.cancelled(run_id):
                raise Cancelled()
            self.debit(
                run_id,
                self.estimate(
                    run, f"{run['pass']}_per_persona_microusd", self.service.settings.per_persona_microusd
                ),
            )
            result = await self.io.journey(run, persona, lambda: self.cancelled(run_id), emit)
            if self.cancelled(run_id):
                raise Cancelled()
            with self.store.transaction() as db:
                current = self.store.load(db, "runs", run_id)
                current["outcomes"][persona["id"]] = result
                current["updated_at"] = now()
                self.store.save(db, "runs", current)
                if result["outcome"] == "failed":
                    finding = {
                        "id": identifier(),
                        "run_id": run_id,
                        "persona_id": persona["id"],
                        "classification": result["classification"],
                        "summary": "Payment confirmed; matching account has no purchased access.",
                        "scope": "unresolved",
                        "evidence": result,
                        "measured": result.get("measured", False),
                    }
                    db.execute(
                        "INSERT INTO findings VALUES (?,?,?)", (finding["id"], run_id, json.dumps(finding))
                    )
            await emit("verification", result, persona["id"])
        # A terminal poll must see comparison and released allowance together.
        with self.store.transaction() as db:
            current = self.store.load(db, "runs", run_id)
            if current["phase"] == "cancelled":
                raise Cancelled()
            current.update(phase="completed", updated_at=now())
            if run["pass"] == "after":
                before = self.store.load(db, "runs", run["parent_run_id"])
                b, a = self.service.view(db, before), self.service.view(db, current)
                current["comparison"] = {
                    "before_run_id": before["id"],
                    "after_run_id": run_id,
                    "population_id": run["population_id"],
                    "requested": run["persona_count"],
                    "same_personas": True,
                    "before_counts": b["counts"],
                    "after_counts": a["counts"],
                    "known_failure_replay": "recorded" if hasattr(self.io, "known_replay") else "not_run",
                    "total_spent_microusd": a["budget"]["spent_microusd"],
                    "unused_microusd": a["budget"]["unused_microusd"],
                }
                db.execute(
                    "UPDATE reservations SET released=reserved-spent WHERE id=?", (run["reservation_id"],)
                )
            self.store.save(db, "runs", current)

    async def serve(self):
        # A killed worker never resumes ambiguous payment actions automatically.
        with self.store.transaction() as db:
            interrupted = db.execute("SELECT run_id,kind FROM jobs WHERE status='running'").fetchall()
            for row in interrupted:
                run = self.store.load(db, "runs", row[0])
                if run["phase"] != "cancelled":
                    gate = {
                        "code": "interrupted",
                        "message": "Worker interrupted during external I/O.",
                        "action": "Verify payment state before any deliberate retry.",
                    }
                    if row["kind"] in ("repair", "repair_recovery"):
                        run["repair"]["status"] = "blocked"
                        run["repair"]["gates"].append(gate)
                        self.store.save(db, "repairs", run["repair"])
                    else:
                        run.update(phase="blocked", updated_at=now())
                        run["gates"].append(gate)
                    self.store.save(db, "runs", run)
            db.execute("UPDATE jobs SET status='blocked' WHERE status='running'")
        while True:
            if not await self.tick():
                await asyncio.sleep(0.5)
