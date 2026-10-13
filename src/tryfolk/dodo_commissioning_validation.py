"""A completed real diagnostic checkout can establish guarded SDK readiness.

This evidence never establishes population coverage or replaces an Entire repair.
Every retained artifact is revalidated; flags alone cannot establish readiness.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, urlsplit

from .dodo import DodoTarget, OWNERSHIP, correlate
from .dodo_runtime_validation import ADAPTER_FILES, digest
from .scenarios import digest as semantic_digest
from .target_provenance import UPSTREAM, dodo_provenance

SCHEMA = "tryfolk.dodo-runtime-validation.v2"
SDK_PINS = {"browser-use": "0.13.11", "stagehand": "4.2.0", "tinytroupe": "0.7.0"}
TINY_COMMIT = "a6244b358a1fe1c71bf751f7ba0f8dfa368ec5a4"


def require(condition, message):
    if not condition:
        raise ValueError("Dodo commissioning remains gated: " + message)


def installed_sdk_identity(root):
    identities = {}
    code = """import importlib.metadata as m,json,sys
d=m.distribution(sys.argv[1]); raw=d.read_text('direct_url.json')
print(json.dumps({'version':d.version,'direct_url':json.loads(raw) if raw else None}))
"""
    for name in SDK_PINS:
        process = subprocess.run(
            [str(Path(root) / "integrations" / name / ".venv/bin/python"), "-c", code, name],
            capture_output=True, text=True, check=True, timeout=10,
        )
        identities[name] = json.loads(process.stdout)
    require(all(identities[name]["version"] == version for name, version in SDK_PINS.items()),
            "installed SDK versions differ from validated pins")
    require((identities["tinytroupe"].get("direct_url") or {}).get("vcs_info", {}).get("commit_id")
            == TINY_COMMIT, "installed TinyTroupe commit differs from the pinned source")
    return identities


def source_fingerprint(root):
    # Use the target's exact Node ordering and bytes contract (config.ts), without
    # importing application configuration or exposing server credentials.
    code = """const {createHash}=require('node:crypto');
const {existsSync,readFileSync,readdirSync}=require('node:fs');
const {join}=require('node:path');const root=process.argv[1],hash=createHash('sha256');
function walk(relative){const p=join(root,relative);if(!existsSync(p))return;
for(const e of readdirSync(p,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
const n=relative+'/'+e.name;if(e.isDirectory())walk(n);
else if(e.isFile())hash.update(n).update('\\0').update(readFileSync(join(root,n)));}}
for(const d of ['app','lib','scripts'])walk(d);
for(const f of ['package.json','package-lock.json','next.config.mjs','tsconfig.json'])
if(existsSync(join(root,f)))hash.update(f).update('\\0').update(readFileSync(join(root,f)));
process.stdout.write(hash.digest('hex'));
"""
    return subprocess.run(["node", "-e", code, str(Path(root) / "dodo-target")],
                          capture_output=True, text=True, check=True, timeout=10).stdout


def _json(path):
    digest(path)
    return json.loads(Path(path).read_text())


def _http(envelope, origin, path, query):
    parsed = urlsplit(envelope.get("url", ""))
    require(envelope.get("http_status") == 200 and envelope.get("method") == "GET"
            and envelope.get("redirects_followed") == 0
            and f"{parsed.scheme}://{parsed.netloc}" == origin and parsed.path == path
            and not parsed.fragment and parse_qs(parsed.query) == {k: [v] for k, v in query.items()},
            "independent oracle HTTP response is missing or belongs to another request")
    return envelope["body"]


def _trace(commission, journey, artifact_paths):
    run_id, persona_id = commission["run_id"], journey["persona_id"]
    readbacks = [r for r in commission.get("trace_readbacks", []) if r.get("persona_id") == persona_id]
    require(len(readbacks) == 1, "persona requires one finalized hosted trace readback")
    readback = readbacks[0]
    trace_id = readback.get("trace_id", "")
    require(bool(re.fullmatch(r"[a-f0-9]{32}", trace_id)), "invalid trace identity")
    require(readback.get("run_id") == run_id and readback.get("source") == "official_neatlogs_trace_api"
            and readback.get("delivery_flushed") is True and readback.get("finalized_readback") == "verified"
            and readback.get("http_status") == 200 and readback.get("hosted_status") == "success"
            and journey.get("trace_url") == readback.get("viewer_url"), "hosted successful trace is unverified")
    viewer = urlsplit(readback["viewer_url"])
    require(viewer.scheme == "https" and viewer.netloc == "app.neatlogs.com"
            and viewer.path == "/traces/" + trace_id, "trace viewer is not the actual hosted trace")
    evidence = Path(commission["events_file"]).parent
    receipt_path = evidence / f"{run_id}-{trace_id}-trace.json"
    raw_path = evidence / f"{run_id}-{trace_id}-readback.json"
    receipt, raw = _json(receipt_path), _json(raw_path)
    artifact_paths.extend([receipt_path, raw_path])
    require(receipt.get("run_id") == run_id and receipt.get("persona_id") == persona_id
            and receipt.get("trace_id") == trace_id and receipt.get("delivery_flushed") is True
            and receipt.get("finalized_readback") == "verified" and not receipt.get("payload_gates"),
            "retained trace receipt is unverified or mismatched")
    spans = [s for s in raw.get("spans", [])
             if (s.get("node_name") or s.get("span_name")) != "neatlogs.trace.complete"]
    actual = {s["span_id"]: s.get("node_name") or s.get("span_name") for s in spans}
    require(raw.get("_id") == trace_id and raw.get("status") == "success"
            and bool(actual) and len(actual) == len(spans) and actual == receipt.get("expected_spans"),
            "hosted finalized payload does not contain every emitted persona span")
    verification = [s for s in spans if actual[s["span_id"]] == "tryfolk.verification"]
    require(len(verification) == 1, "hosted persona trace lacks its outcome verification span")
    data = verification[0].get("data") or {}
    incoming = data.get("input")
    outgoing = data.get("output")
    incoming = json.loads(incoming) if isinstance(incoming, str) else incoming
    outgoing = json.loads(outgoing) if isinstance(outgoing, str) else outgoing
    outcome = journey["outcome"]
    require(isinstance(incoming, dict) and isinstance(outgoing, dict)
            and incoming.get("run_id") == run_id and incoming.get("persona_id") == persona_id
            and incoming.get("provider") == "dodo" and incoming.get("revision") == commission["run"]["revision"]
            and outgoing.get("outcome") == "completed"
            and all(outgoing.get(k) is True for k in ("measured", "payment_verified", "entitlement_verified", "capability_available"))
            and outgoing.get("observed_payload_sha256") == hashlib.sha256(json.dumps(outcome, default=str).encode()).hexdigest(),
            "hosted verification does not bind the actual completed persona outcome")
    return trace_id


def assess_commissioning(commission, root, *, sdk_identity=None):
    root = Path(root)
    require(commission.get("schema") == "tryfolk.dodo-commissioning.v1"
            and commission.get("diagnostic") is True and commission.get("not_in_population_denominator") is True
            and commission.get("execution_ready_declared") is False
            and commission.get("status") == "journeys_recorded" and not commission.get("blockers")
            and not commission.get("error"), "no successful genuine diagnostic checkout")
    count = commission.get("requested")
    require(type(count) is int and count in (1, 4) and commission.get("measured") == count,
            "diagnostic checkout count is incomplete")
    files = commission.get("adapter_files_before", {})
    require(set(files) == set(ADAPTER_FILES) and commission.get("adapter_sources_unchanged") is True
            and commission.get("adapter_files_after") == files
            and all(files[name] == digest(root / name) for name in ADAPTER_FILES),
            "guarded adapter source changed during or after commissioning")
    require(commission.get("installed_sdk_versions") == SDK_PINS, "commissioning used different SDK pins")
    identity = sdk_identity if sdk_identity is not None else installed_sdk_identity(root)
    require(all(identity.get(n, {}).get("version") == v for n, v in SDK_PINS.items())
            and (identity.get("tinytroupe", {}).get("direct_url") or {}).get("vcs_info", {}).get("commit_id")
            == TINY_COMMIT, "current SDK identities differ from required pins")
    readiness = commission.get("readiness", {})
    require(readiness.get("ready") is True and not readiness.get("errors"), "target was not ready")
    manifest = readiness["manifest"]
    dodo_provenance(manifest, root / "dodo-target")
    origin = manifest["app_origin"]
    DodoTarget(origin)
    require(manifest.get("mode") == "healthy" and manifest.get("fault") is None
            and manifest.get("safe_origins") == [origin]
            and manifest.get("checkout_origins") == ["https://test.checkout.dodopayments.com"]
            and manifest.get("product", {}).get("amount_minor") == 100
            and manifest["product"].get("currency") == "USD"
            and manifest["product"].get("type") == "one-time", "only the owned USD 1.00 Test Mode control is valid")
    build_path = root / "dodo-target/.target-manifest.json"
    build = _json(build_path)
    require(build.get("source_clean") is True and build.get("source_commit") == manifest["source_commit"]
            and build.get("source_sha256") == manifest["source_sha256"] == source_fingerprint(root),
            "current target source differs from the commissioned clean build")
    run, run_id = commission["run"], commission["run_id"]
    require(bool(re.fullmatch(r"[a-f0-9]{32}", run_id)) and run.get("id") == run_id
            and run.get("provider") == "dodo" and run.get("preview_url") == origin
            and run.get("revision") == manifest["target_revision"]
            and run.get("source_digest") == manifest["source_sha256"], "run/build binding differs")
    population_path, events_path = Path(commission["population_file"]), Path(commission["events_file"])
    value = _json(population_path)
    population = value if isinstance(value, list) else value["population"]
    require(len(population) == 4 and len({p["id"] for p in population}) == 4
            and all(p.get("initial_snapshot") and p.get("initial_semantic_memory") for p in population)
            and semantic_digest(population) == commission.get("population_sha256"),
            "saved four-persona cohort or initial memories changed")
    require(digest(events_path) == commission.get("events_sha256"), "recorded journey events changed")
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line]
    paths = [population_path, events_path, build_path, root / "dodo-target/LICENSE.md"]
    journeys = commission.get("journeys", [])
    require(len(journeys) == count and {j["persona_id"] for j in journeys} == {p["id"] for p in population[:count]},
            "recorded journeys differ from the selected saved personas")
    trace_ids, accounts, orders = [], set(), set()
    for journey in journeys:
        persona_id = journey["persona_id"]
        persona = next(p for p in population if p["id"] == persona_id)
        require(not journey.get("error") and journey.get("initial_state_sha256") == semantic_digest(persona),
                "journey failed or initial persona changed")
        outcome = journey["outcome"]
        require(outcome.get("outcome") == "completed" and all(outcome.get(k) is True for k in
                ("measured", "payment_verified", "entitlement_verified", "capability_checked", "capability_available")),
                "payment, grant and actual usable capability were not all established")
        own_events = [e for e in events if e.get("persona_id") == persona_id]
        require(any(e.get("kind") == "verification" and e.get("data") == outcome for e in own_events),
                "outcome does not match the recorded verification event")
        pays = [e for e in own_events if e.get("kind") == "browser_action"
                and e.get("data", {}).get("action", {}).get("_tryfolk", {}).get("kind") == "pay"]
        require(len(pays) == 1 and pays[0]["data"]["result"].get("data", {}).get("success") is True,
                "one real acknowledged guarded Pay action is required")
        order = outcome["order"]
        expected = {"run_id": run_id, "persona_id": persona_id, "target_revision": manifest["target_revision"]}
        require(all(order.get(k) == v for k, v in expected.items()) and order.get("active") is True
                and order.get("amount_minor") == 100 and order.get("currency") == "USD"
                and order.get("expected_product_id") == manifest["product"]["product_id"], "checkout ownership differs")
        http = outcome["oracle_http"]
        recorded_orders = _http(http["orders_after"], origin, "/api/target/orders", {"account_id": order["account_id"]})["orders"]
        require(len(recorded_orders) == 1 and all(recorded_orders[0].get(k) == v for k, v in order.items()),
                "independent order response differs from the checkout")
        receipt = _http(http["provider_receipt"], origin, "/api/target/receipts", {"order_id": order["order_id"]})
        entitlement = _http(http["app_entitlement"], origin, "/api/target/entitlements",
                            {"account_id": order["account_id"], "order_id": order["order_id"]})
        verified = correlate(recorded_orders[0], receipt, entitlement, True)
        require(verified.get("outcome") == "completed" and all(verified.get(k) is True for k in
                ("measured", "payment_verified", "entitlement_verified", "capability_available")),
                "actual provider payment and application access do not correlate")
        capability = outcome["capability_evidence"]
        file_path = Path(capability["download_path"])
        require(capability.get("available") is True and capability.get("source") == "stagehand_browser_download"
                and digest(file_path) == capability.get("download_sha256")
                and file_path.stat().st_size == capability.get("bytes")
                and 0 < file_path.stat().st_size <= 1_000_000 and file_path.read_bytes().startswith(b"<svg"),
                "actual usable downloaded artwork is absent or changed")
        admission = capability.get("download_admission") or {}
        binding = capability.get("download_binding") or {}
        require(admission.get("decision") == "owned_download_event"
                and admission.get("admission_phase") == "actual_primary_download_event_before_save"
                and admission.get("first_failed_predicate") is None
                and bool(admission.get("owned_navigation_checks"))
                and all(v is True for v in admission["owned_navigation_checks"].values())
                and admission.get("download_binding") == binding
                and all(binding.get(k) == order[k] for k in (*OWNERSHIP, "order_id", "checkout_session_id", "expected_product_id"))
                and binding.get("payment_id") == receipt["payment"]["payment_id"]
                and binding.get("download_url", "").startswith(origin + "/api/target/download?"),
                "protected file admission is not bound to this paid account and order")
        require(order["account_id"] not in accounts and order["order_id"] not in orders,
                "diagnostic personas shared an account or order")
        accounts.add(order["account_id"])
        orders.add(order["order_id"])
        paths.append(file_path)
        trace_ids.append(_trace(commission, journey, paths))
    require(len(set(trace_ids)) == count, "persona traces are not independent")
    return {"status": "passed", "source_digest": manifest["source_sha256"], "source_commit": manifest["source_commit"],
            "upstream_commit": UPSTREAM, "scope": "real_completed_healthy_diagnostic_checkout",
            "population_validation": "pending", "commissioning_run_id": run_id,
            "completed_diagnostic_personas": count, "population_sha256": commission["population_sha256"],
            "validated_product": manifest["product"],
            "signed_webhook_verified": False, "adapter_files": files, "installed_sdk_identity": identity,
            "artifact_paths": [str(p.resolve()) for p in paths]}


def write_validation(commissioning, *, output, root):
    source = Path(commissioning).resolve()
    result = assess_commissioning(_json(source), root)
    paths = [source, *(Path(p) for p in result.pop("artifact_paths"))]
    result.update(schema=SCHEMA, commissioning_file=str(source),
                  evidence=[{"path": str(p), "sha256": digest(p)} for p in dict.fromkeys(paths)])
    output = Path(output)
    require(not output.is_symlink(), "validation output cannot be a symlink")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        json.dump(result, handle, indent=2)
    output.chmod(0o600)
    return result


def commissioning_execution_ready(manifest, proof, root):
    require(proof.get("schema") == SCHEMA and proof.get("status") == "passed", "invalid validation declaration")
    evidence = proof.get("evidence", [])
    paths = {item["path"] for item in evidence}
    require(len(paths) == len(evidence) and all(digest(item["path"]) == item["sha256"] for item in evidence),
            "retained commissioning evidence changed")
    result = assess_commissioning(_json(proof["commissioning_file"]), root)
    require({proof["commissioning_file"], *result["artifact_paths"]} == paths,
            "validation does not bind every actual source artifact")
    require(all(proof.get(k) == result[k] for k in ("source_digest", "source_commit", "adapter_files",
            "installed_sdk_identity", "commissioning_run_id", "completed_diagnostic_personas", "population_sha256", "validated_product")),
            "validation summary differs from actual retained evidence")
    dodo_provenance(manifest, Path(root) / "dodo-target")
    require(manifest.get("source_sha256") == result["source_digest"]
            and manifest.get("source_commit") == result["source_commit"]
            and manifest.get("product") == result["validated_product"]
            and manifest.get("product", {}).get("amount_minor") == 100
            and manifest["product"].get("currency") == "USD", "registered build differs from commissioned source")
    return True
