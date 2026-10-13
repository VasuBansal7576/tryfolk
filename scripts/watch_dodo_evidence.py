"""Finite local read-only watcher of actual scoped provider diagnostics."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--database", default="runtime/dodo-broken.sqlite")
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    if len(args.run_id) != 32 or any(c not in "0123456789abcdef" for c in args.run_id):
        raise ValueError("Exact run ID required")
    if not 1 <= args.seconds <= 600:
        raise ValueError("Finite watcher bound required")
    database = Path(args.database).resolve()
    connection = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True)
    product = sqlite3.connect("file:runtime/tryfolk.sqlite?mode=ro", uri=True)
    run = json.loads(product.execute("SELECT payload FROM runs WHERE id=?", (args.run_id,)).fetchone()[0])
    output = Path("runtime/evidence") / (args.run_id + "-native-provider-evidence-watch.json")
    result = {"run_id": args.run_id, "source": "actual_owned_target_SQLite_evidence",
              "database": str(database), "armed_at": datetime.now(timezone.utc).isoformat(),
              "expected_revision": run.get("revision"), "no_provider_requests": True,
              "no_database_mutations": True, "provider_errors": [], "receipt_counts": {},
              "read_observations": [], "read_observation_counts": {},
              "unique_provider_read_observations": 0, "native_pay_admission_events": [],
              "hosted_checkout_admission_events": []}

    def save():
        output.write_text(json.dumps(result, indent=2))
        output.chmod(0o600)

    save()
    print(json.dumps({"armed": True, "run_id": args.run_id, "evidence": str(output)}), flush=True)
    seen = set()
    seen_runtime_events = set()
    provider_reads = set()
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        for event_id, persona_id, created_at, kind, raw in product.execute(
            "SELECT id,persona_id,created_at,kind,data FROM events WHERE run_id=? "
            "AND kind IN ('native_pay_admission','hosted_checkout_admission')",
            (args.run_id,),
        ):
            if event_id in seen_runtime_events:
                continue
            data = json.loads(raw)
            result[kind + "_events"].append({
                "event_id": event_id, "persona_id": persona_id, "created_at": created_at,
                "data": {key: data[key] for key in (
                    "phase", "capacity", "wait_ms", "duration_ms", "result", "payment_outcome_inferred",
                    "cooldown_ms", "configured_minimum_spacing_ms", "spacing_basis",
                    "boundary_kind",
                    "queue_bound_ms", "ownership_scope",
                ) if key in data},
            })
            seen_runtime_events.add(event_id)
        orders = {order["order_id"]: order for row in connection.execute(
            "SELECT data FROM records WHERE collection='orders'")
            if (order := json.loads(row[0])).get("run_id") == args.run_id}
        for evidence_id, raw in connection.execute("SELECT id,data FROM evidence"):
            if evidence_id in seen:
                continue
            entry = json.loads(raw)
            ownership = entry.get("ownership") or {}
            if ownership.get("run_id") != args.run_id:
                continue
            order = orders.get(ownership.get("order_id"))
            if order is None:
                continue
            exact = all(ownership.get(key) == order.get(key) for key in
                ("run_id", "persona_id", "account_id", "fixture_id", "target_revision", "order_id"))
            if not exact or (run.get("revision") and order.get("target_revision") != run["revision"]):
                result["scope_error"] = "owned_order_revision_or_identity_changed"
                save()
                raise RuntimeError("Scoped evidence identity changed")
            seen.add(evidence_id)
            state = entry.get("state")
            if state in ("pending", "succeeded", "failed", "mismatch", "provider_error"):
                result["receipt_counts"][state] = result["receipt_counts"].get(state, 0) + 1
            observation = entry.get("provider_read_observation")
            if observation:
                item = {"evidence_id": evidence_id, "observed_at": entry.get("observed_at"),
                        "persona_id": ownership.get("persona_id"), "order_id": order["order_id"],
                        "source": entry.get("source"), "state": state,
                        "exact_owned_scope": exact, **observation}
                result["read_observations"].append(item)
                category = "cache_hit" if observation.get("cache_hit") else (
                    "coalesced" if observation.get("coalesced") else "independent_read")
                result["read_observation_counts"][category] = result["read_observation_counts"].get(category, 0) + 1
                provider_reads.add((order["order_id"], observation.get("provider_read_started_at"),
                                    observation.get("provider_read_finished_at")))
                # Counts complete SDK read observations, not inferred HTTP calls.
                result["unique_provider_read_observations"] = len(provider_reads)
            if state == "provider_error":
                diagnostic = entry.get("provider_read_diagnostics")
                item = {"evidence_id": evidence_id, "observed_at": entry.get("observed_at"),
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "persona_id": ownership.get("persona_id"), "order_id": ownership.get("order_id"),
                        "reason": entry.get("reason"), "source": entry.get("source"),
                        "exact_owned_scope": exact, "provider_read_diagnostics": diagnostic}
                result["provider_errors"].append(item)
                save()
                print(json.dumps({"provider_error": item}), flush=True)
        save()
        phase = json.loads(product.execute("SELECT payload FROM runs WHERE id=?", (args.run_id,)).fetchone()[0]).get("phase")
        if phase in ("blocked", "cancelled", "completed", "complete"):
            result["terminal_phase"] = phase
            break
        time.sleep(0.2)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({"run_id": args.run_id, "provider_errors": len(result["provider_errors"]),
                      "evidence": str(output)}), flush=True)


if __name__ == "__main__":
    main()
