"""Retain actual completed run events, conservative overlap and hosted trace reads.

Read-only SQLite observation. Does not execute journeys or change any outcome.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

from tryfolk.dodo_runtime_validation import digest
from tryfolk.process import ROOT
from tryfolk.scenarios import digest as semantic_digest
from tryfolk.telemetry import read_trace


def save(path, value):
    if path.is_symlink():
        raise ValueError("Evidence output must not be a symlink")
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)
    return {"path": str(path), "sha256": digest(path)}


async def collect(run_id, database, evidence):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Exact owned run ID required")
    db = sqlite3.connect("file:" + str(database.resolve()) + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Run is not present in the owned store")
        run = json.loads(row[0])
        if run["phase"] not in {"completed", "blocked", "failed", "cancelled"}:
            raise ValueError("Run still active; terminal evidence collection deferred")
        row = db.execute("SELECT payload FROM populations WHERE id=?", (run["population_id"],)).fetchone()
        population = json.loads(row[0]) if row else []
        events = [dict(row) for row in db.execute(
            "SELECT id,run_id,persona_id,kind,created_at,data,trace_url FROM events WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()]
        for event in events:
            event["data"] = json.loads(event["data"])
    finally:
        db.close()
    evidence.mkdir(parents=True, exist_ok=True)
    paths = [save(evidence / f"{run_id}-recorded-run.json", run),
             save(evidence / f"{run_id}-recorded-population.json", population),
             save(evidence / f"{run_id}-recorded-events.json", events)]
    expected = {persona["id"] for persona in population}
    receipts = []
    for path in evidence.glob(f"{run_id}-*-trace.json"):
        receipt = json.loads(path.read_text())
        if receipt.get("persona_id") in expected and receipt.get("run_id") == run_id:
            receipts.append(receipt)
    reads = await asyncio.gather(*(read_trace(run_id, receipt["trace_id"]) for receipt in receipts))
    receipt_counts = {pid: sum(r.get("persona_id") == pid for r in receipts) for pid in expected}
    verified_personas = {r.get("persona_id") for r in reads if r.get("finalized_readback") == "verified"}
    contexts = []
    for receipt in receipts:
        trace = receipt["trace_id"]
        for suffix in ("trace", "readback"):
            path = evidence / f"{run_id}-{trace}-{suffix}.json"
            if path.is_file():
                paths.append({"path": str(path), "sha256": digest(path)})
        raw = evidence / f"{run_id}-{trace}-readback.json"
        if raw.is_file() and any(r.get("trace_id") == trace and r.get("finalized_readback") == "verified" for r in reads):
            payload = json.loads(raw.read_text())
            roots = [s for s in payload.get("spans", []) if s.get("node_name") == "tryfolk.persona_journey"
                     and not s.get("parent_span_id")]
            if len(roots) == 1:
                span = roots[0]
                contexts.append({"persona_id": receipt["persona_id"], "trace_id": trace,
                                 "span_id": span["span_id"], "created_at": span["createdAt"],
                                 "updated_at": span["updatedAt"], "hosted_span_status": span.get("status"),
                                 "actual_duration_seconds": span.get("data", {}).get("duration_seconds")})
    intervals = []
    for persona in population:
        pid = persona["id"]
        starts = [e for e in events if e["persona_id"] == pid and e["kind"] == "persona_initialized"]
        ends = [e for e in events if e["persona_id"] == pid and e["kind"] == "verification"]
        intervals.append({"persona_id": pid, "initialized_at": starts[0]["created_at"] if len(starts) == 1 else None,
                          "verified_at": ends[0]["created_at"] if len(ends) == 1 else None,
                          "initialization_event_ids": [e["id"] for e in starts], "verification_event_ids": [e["id"] for e in ends]})
    established = len(intervals) == run["persona_count"] and all(i["initialized_at"] and i["verified_at"] for i in intervals)
    seconds = None
    if established:
        latest_start = max(datetime.fromisoformat(i["initialized_at"]) for i in intervals)
        earliest_end = min(datetime.fromisoformat(i["verified_at"]) for i in intervals)
        seconds = max(0, (earliest_end - latest_start).total_seconds())
    samples = evidence / f"{run_id}-sdk-process-observations.jsonl"
    process_samples = []
    if samples.exists():
        paths.append({"path": str(samples), "sha256": digest(samples)})
        for line in samples.read_text().splitlines():
            sample = json.loads(line)
            processes = sample.get("processes", [])
            process_samples.append({"observed_at": sample["observed_at"], "phase": sample.get("recorded_run_phase"),
                                    "tinytroupe": sum(p["sdk"] == "tinytroupe" for p in processes),
                                    "stagehand": sum(p["sdk"] == "stagehand" for p in processes),
                                    "parent_pids": sorted({p["parent_pid"] for p in processes})})
    context_seconds = None
    if len(contexts) == len(expected) and {c["persona_id"] for c in contexts} == expected:
        latest = max(datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")) for c in contexts)
        earliest = min(datetime.fromisoformat(c["updated_at"].replace("Z", "+00:00")) for c in contexts)
        context_seconds = max(0, (earliest - latest).total_seconds())
    result = {
        "schema": "tryfolk.recorded-run-trace-evidence.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "actual_read_only_owned_sqlite_and_official_neatlogs_trace_api", "run_id": run_id,
        "pass": run["pass"], "provider": run["provider"], "phase": run["phase"], "requested": run["persona_count"],
        "population_id": run["population_id"], "population_sha256": semantic_digest(population),
        "persona_bindings": [{"persona_id": p["id"], "saved_state_sha256": semantic_digest(p),
                             "profile_sha256": semantic_digest(p.get("profile")),
                             "conditions_sha256": semantic_digest(p.get("conditions"))} for p in population],
        "recorded_outcomes": {pid: {key: value.get(key) for key in ("outcome", "classification", "measured", "payment_verified",
                              "entitlement_verified", "capability_checked", "capability_available", "account_id", "revision")}
                              for pid, value in run.get("outcomes", {}).items()},
        "trace_readbacks": reads, "readable_persona_trace_count": len(verified_personas & expected),
        "all_requested_persona_traces_verified": verified_personas == expected and all(n == 1 for n in receipt_counts.values()),
        "missing_persona_receipts": sorted(pid for pid, count in receipt_counts.items() if count == 0),
        "ambiguous_persona_receipts": sorted(pid for pid, count in receipt_counts.items() if count > 1),
        "concurrency": {"source": "persisted_persona_initialized_and_verification_timestamps",
                        "intervals": intervals, "all_personas_common_active_seconds": seconds,
                        "all_requested_journeys_overlap_established": bool(established and seconds > 0),
                        "initialization_includes_complete_memory_and_browser_startup": True,
                        "sdk_process_observations_have_no_embedded_persona_or_run_ids": True,
                        "actual_process_samples": process_samples,
                        "hosted_persona_context_intervals": contexts,
                        "all_persona_context_overlap_seconds_at_hosted_one_second_precision": context_seconds,
                        "context_overlap_interpretation": "Trace contexts include startup and cancellation cleanup; context overlap does not establish completed checkouts or continuous useful browser work. Hosted success is not a journey outcome."},
        "evidence": paths,
    }
    path = evidence / f"{run_id}-trace-and-concurrency-evidence.json"
    save(path, result)
    print(json.dumps({"evidence": str(path), "requested": run["persona_count"],
                      "recorded_outcomes": len(result["recorded_outcomes"]), "readable_traces": result["readable_persona_trace_count"],
                      "common_active_seconds": seconds}))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--database", type=Path, default=ROOT / "runtime/tryfolk.sqlite")
    parser.add_argument("--evidence", type=Path, default=ROOT / "runtime/evidence")
    args = parser.parse_args()
    os.environ.setdefault("TRYFOLK_NEATLOGS_ENV", str(ROOT / "runtime/neatlogs.env"))
    os.environ["TRYFOLK_EVIDENCE_ROOT"] = str(args.evidence.resolve())
    asyncio.run(collect(args.run_id, args.database, args.evidence.resolve()))


if __name__ == "__main__":
    main()
