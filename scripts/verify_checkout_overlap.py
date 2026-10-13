"""Verify actual four-checkout overlap, independent receipts, and paired traces.

Uses retained real events/HTTP envelopes, never SDK-process coexistence. Running
with --refresh-traces reads only official Neatlogs; no provider/payment traffic.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from tryfolk.dodo import OWNERSHIP, correlate
from tryfolk.dodo_commissioning_validation import _http
from tryfolk.process import ROOT
from tryfolk.scenarios import digest


HOST = "https://test.checkout.dodopayments.com"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(date.tzinfo is not None, "Event timestamp lacks timezone")
    return date


def hosted_intervals(run, events, personas):
    require(len(personas) == len(set(personas)) == 4, "Exactly four independent hosted checkouts are required")
    intervals = []
    for pid in personas:
        observations = [e for e in events if e["persona_id"] == pid and e["kind"] == "observation"]
        hosted = []
        for e in observations:
            data = e["data"]
            parsed = urlsplit(data.get("url", ""))
            if f"{parsed.scheme}://{parsed.netloc}" != HOST:
                continue
            document = data.get("document") or {}
            require(document.get("url") == data["url"] and document.get("hosted_test_mode_visible") is True
                    and document.get("body_present") is True and document.get("ready_state") == "complete"
                    and data.get("freshness", {}).get("status") == "fresh",
                    "Primary hosted document observation is not genuinely fresh/Test Mode/ready")
            hosted.append(e)
        require(len(hosted) >= 2, "Each persona needs at least two actual hosted primary-document observations")
        first, last = hosted[0], hosted[-1]
        order = run["outcomes"][pid]["order"]
        require(order["checkout_session_id"] in urlsplit(first["data"]["url"]).path.split("/"),
                "First hosted primary document does not belong to this independently recorded checkout")
        between = [e for e in observations if first["id"] <= e["id"] <= last["id"]]
        require(all(urlsplit(e["data"].get("url", "")).hostname == "test.checkout.dodopayments.com" for e in between),
                "Hosted document occupancy is discontinuous")
        require(timestamp(first["created_at"]) < timestamp(last["created_at"]), "Hosted window must have positive duration")
        intervals.append({"persona_id": pid, "first_observed_at": first["created_at"],
                          "last_observed_at": last["created_at"], "first_event_id": first["id"],
                          "last_event_id": last["id"], "hosted_observation_event_ids": [e["id"] for e in hosted],
                          "checkout_session_sha256": sha(order["checkout_session_id"].encode()),
                          "window_basis": "conservative first-to-last freshly observed primary hosted document; actual checkout-bound session, no intervening off-host observation"})
    latest = max(timestamp(i["first_observed_at"]) for i in intervals)
    earliest = min(timestamp(i["last_observed_at"]) for i in intervals)
    signed = (earliest - latest).total_seconds()
    return {"intervals": intervals, "signed_all_four_intersection_seconds": signed,
            "all_four_hosted_intersection_seconds": max(0, signed), "all_four_hosted_checkouts_overlap": signed > 0,
            "sdk_process_overlap_or_persona_context_duration_used": False}


def inspect(run_id, evidence):
    paths = {name: evidence / f"{run_id}-recorded-{name}.json" for name in ("run", "population", "events")}
    run, population, events = (json.loads(paths[name].read_text()) for name in ("run", "population", "events"))
    require(run["id"] == run_id and run["phase"] == "completed" and run["provider"] == "dodo"
            and run["persona_count"] == len(population) == 4, "A completed genuine four-person Dodo run is required")
    require(run["pass"] in {"before", "after"}, "Invalid pass")
    ids = sorted(p["id"] for p in population)
    require(set(run["outcomes"]) == set(ids) and len(set(ids)) == 4, "Exactly the original four measured outcomes are required")
    accounts, orders, sessions, payments = set(), set(), set(), set()
    bindings, observed_spans, downloads, retained_files = [], [], [], []
    for pid in ids:
        outcome = run["outcomes"][pid]
        order, http = outcome["order"], outcome["oracle_http"]
        require(order["run_id"] == run_id and order["persona_id"] == pid and order["target_revision"] == run["revision"], "Order ownership mismatch")
        require(order["amount_minor"] == 100 and order["currency"] == "USD", "Test purchase amount differs")
        origin = run["preview_url"]
        recorded = _http(http["orders_after"], origin, "/api/target/orders", {"account_id": order["account_id"]})["orders"]
        require(len(recorded) == 1 and all(recorded[0].get(k) == v for k, v in order.items()), "Independent order HTTP evidence differs")
        receipt = _http(http["provider_receipt"], origin, "/api/target/receipts", {"order_id": order["order_id"]})
        entitlement = _http(http["app_entitlement"], origin, "/api/target/entitlements", {"account_id": order["account_id"], "order_id": order["order_id"]})
        verified = correlate(order, receipt, entitlement, True if run["pass"] == "after" else None)
        require(verified["measured"] is True and verified["payment_verified"] is True, "Actual provider payment is unverified")
        if run["pass"] == "after":
            require(verified["outcome"] == outcome["outcome"] == "completed" and entitlement["grant_count"] == entitlement["account_grant_count"] == 1, "One genuine grant per paid after account required")
            require(all(outcome.get(k) is True for k in ("measured", "payment_verified", "entitlement_verified", "capability_checked", "capability_available")), "Recorded after outcome lacks verified actual payment/grant/capability")
            cap = outcome["capability_evidence"]
            path = Path(cap["download_path"])
            require(cap["available"] is True and cap["source"] == "stagehand_browser_download" and path.is_file()
                    and not path.is_symlink() and path.stat().st_size == cap["bytes"]
                    and sha(path.read_bytes()) == cap["download_sha256"] and path.read_bytes().startswith(b"<svg"), "Actual protected download is unavailable/changed")
            downloads.append({"persona_id": pid, "path": str(path), "bytes": path.stat().st_size, "sha256": sha(path.read_bytes())})
            admission, binding = cap["download_admission"], cap["download_binding"]
            require(admission["decision"] == "owned_download_event" and admission["first_failed_predicate"] is None
                    and admission["download_binding"] == binding and admission["owned_navigation_checks"]
                    and all(v is True for v in admission["owned_navigation_checks"].values()), "Download was not admitted by all owned-navigation guards")
            require(all(binding[k] == order[k] for k in (*OWNERSHIP, "order_id", "checkout_session_id", "expected_product_id"))
                    and binding["payment_id"] == receipt["payment"]["payment_id"]
                    and binding["download_url"].startswith(origin + "/api/target/download?"), "Protected download belongs to a different purchase")
        else:
            require(verified["classification"] == "paid_without_access" and entitlement["grant_count"] == 0, "Before must contain actual paid-without-access failure")
        for values, value in ((accounts, order["account_id"]), (orders, order["order_id"]),
                              (sessions, order["checkout_session_id"]), (payments, receipt["payment"]["payment_id"])):
            require(value not in values, "Persona account/order/checkout/payment identity is duplicated")
            values.add(value)
        receipts = []
        for path in evidence.glob(f"{run_id}-*-trace.json"):
            item = json.loads(path.read_text())
            if item.get("persona_id") == pid:
                receipts.append(item)
        require(len(receipts) == 1, "Exactly one genuine persona trace receipt is required")
        tr = receipts[0]
        for suffix in ("trace", "readback"):
            file = evidence / f"{run_id}-{tr['trace_id']}-{suffix}.json"
            require(file.is_file() and not file.is_symlink(), "Trace evidence must be an actual retained regular file")
            retained_files.append({"path": str(file), "sha256": sha(file.read_bytes())})
        raw = json.loads((evidence / f"{run_id}-{tr['trace_id']}-readback.json").read_text())
        spans = [s for s in raw["spans"] if s.get("node_name") != "neatlogs.trace.complete"]
        require(tr.get("run_id") == run_id and tr.get("delivery_flushed") is True
                and tr.get("finalized_readback") == "verified" and raw["_id"] == tr["trace_id"]
                and raw.get("status") in {"success", "error"}
                and len({s["span_id"] for s in spans}) == len(spans)
                and {s["span_id"]: s.get("node_name") for s in spans} == tr["expected_spans"], "Finalized hosted trace is absent/mismatched")
        selected = [e for e in events if e["persona_id"] == pid and e["kind"] in {"observation", "verification", "capability", "native_pay_admission", "hosted_checkout_admission"}]
        for event in selected:
            hashed = sha(json.dumps(event["data"], default=str).encode())
            matches = []
            for span in spans:
                if span.get("node_name") != "tryfolk." + event["kind"]:
                    continue
                out = span.get("data", {}).get("output")
                out = json.loads(out) if isinstance(out, str) else out
                if isinstance(out, dict) and out.get("observed_payload_sha256") == hashed:
                    matches.append(span)
            require(len(matches) == 1, "Actual primary-document/HTTP verification event differs from finalized trace payload")
            observed_spans.append({"event_id": event["id"], "trace_id": tr["trace_id"], "span_id": matches[0]["span_id"], "payload_sha256": hashed})
        bindings.append({"persona_id": pid, "account_id": order["account_id"], "order_id": order["order_id"],
                         "checkout_session_id": order["checkout_session_id"], "payment_id": receipt["payment"]["payment_id"], "trace_id": tr["trace_id"]})
    return {"run_id": run_id, "pass": run["pass"], "population_id": run["population_id"], "population_sha256": digest(population),
            "parent_run_id": run.get("parent_run_id"), "bindings": bindings, "verified_event_spans": observed_spans,
            "actual_protected_downloads": downloads, "retained_trace_files": retained_files,
            "checkout_overlap": hosted_intervals(run, events, ids),
            "actual_source_files": [{"path": str(p), "sha256": sha(p.read_bytes())} for p in paths.values()]}


def pair(before_id, after_id, evidence):
    before, after = inspect(before_id, evidence), inspect(after_id, evidence)
    require(before["pass"] == "before" and after["pass"] == "after" and after["parent_run_id"] == before_id,
            "After is not paired to the actual original before")
    require(before["population_id"] == after["population_id"] and before["population_sha256"] == after["population_sha256"], "Saved cohort differs")
    for field in ("account_id", "order_id", "checkout_session_id", "payment_id"):
        require(not ({x[field] for x in before["bindings"]} & {x[field] for x in after["bindings"]}), "After reused a prior purchase identity: " + field)
    return {"schema": "tryfolk.actual-four-hosted-checkout-pair.v1", "observed_at": datetime.now(timezone.utc).isoformat(),
            "before": before, "after": after,
            "acceptance_met": all(x["checkout_overlap"]["all_four_hosted_checkouts_overlap"] for x in (before, after)),
            "no_provider_requests_or_payments_by_collector": True,
            "required_behavior": "all four actual hosted checkout primary-document windows intersect in BOTH passes, with exact saved cohort, eight finalized traces, independent real HTTP payment/grant truth and no duplicate identities"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("before_run_id")
    p.add_argument("after_run_id")
    p.add_argument("--evidence", type=Path, default=ROOT / "runtime/evidence")
    p.add_argument("--refresh-traces", action="store_true")
    args = p.parse_args()
    os.environ["TRYFOLK_EVIDENCE_ROOT"] = str(args.evidence.resolve())
    if args.refresh_traces:
        from collect_run_trace_evidence import collect
        os.environ.setdefault("TRYFOLK_NEATLOGS_ENV", str(ROOT / "runtime/neatlogs.env"))
        for rid in (args.before_run_id, args.after_run_id):
            asyncio.run(collect(rid, ROOT / "runtime/tryfolk.sqlite", args.evidence.resolve()))
    result = pair(args.before_run_id, args.after_run_id, args.evidence.resolve())
    path = args.evidence / f"{args.after_run_id}-strict-hosted-overlap-pair.json"
    path.write_text(json.dumps(result, indent=2))
    path.chmod(0o600)
    print(json.dumps({"acceptance_met": result["acceptance_met"], "evidence": str(path),
                      "before_all_four_hosted_seconds": result["before"]["checkout_overlap"]["all_four_hosted_intersection_seconds"],
                      "after_all_four_hosted_seconds": result["after"]["checkout_overlap"]["all_four_hosted_intersection_seconds"]}))
    return 0 if result["acceptance_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
