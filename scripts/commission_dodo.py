"""Real guarded Dodo commissioning before general target registration.

Commissioning stays explicitly diagnostic. It never writes execution-ready or
SDK-validation declarations and never contributes fixture outcomes to a cohort.
"""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import traceback
from types import SimpleNamespace
from uuid import uuid4

from dotenv import dotenv_values

from tryfolk.dodo import DodoTarget
from tryfolk.dodo_runtime_validation import ADAPTER_FILES
from tryfolk.payment_privacy import redact_payment
from tryfolk.process import ROOT, SDKProcess
from tryfolk.runtime import Runtime
from tryfolk.scenarios import digest
from tryfolk.telemetry import read_trace


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str))
    path.chmod(0o600)


def adapter_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in ADAPTER_FILES}


async def installed_sdks():
    versions = {}
    for integration, distribution in (("browser-use", "browser-use"), ("stagehand", "stagehand"),
                                      ("tinytroupe", "tinytroupe")):
        child = await asyncio.create_subprocess_exec(
            str(ROOT / "integrations" / integration / ".venv/bin/python"),
            "-c", "import importlib.metadata,sys;print(importlib.metadata.version(sys.argv[1]))", distribution,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await child.communicate()
        if child.returncode:
            raise RuntimeError("Cannot read the actual installed SDK version for " + integration)
        versions[integration] = stdout.decode().strip()
    return versions


async def population_at(args, generation_run_id):
    if args.population.exists():
        value = json.loads(args.population.read_text())
        population = value if isinstance(value, list) else value["population"]
    else:
        if not args.brief:
            raise ValueError("Supply a saved population or an actual generation brief.")
        brief = json.loads(args.brief.read_text())
        generator = SDKProcess("tinytroupe")
        try:
            if brief.get("generation_plan"):
                command = {"op": "generate_bulk", "brief": brief, "count": 4,
                           "start_index": 0, "run_id": generation_run_id}
            else:
                command = {"op": "generate", "brief": brief, "count": 4}
            population = (await generator.request(command, timeout=720))["population"]
        finally:
            await generator.close()
        # Saving actual generation once is required even when commissioning one
        # customer first. The later four and repaired pass restore this file.
        save(args.population, {"generation_run_id": generation_run_id, "population": population})
    if len(population) != 4 or len({persona["id"] for persona in population}) != 4:
        raise ValueError("Exactly four distinct saved personas are required.")
    if any(not persona.get("initial_snapshot") or not persona.get("initial_semantic_memory")
           for persona in population):
        raise ValueError("Every saved persona needs its genuine initial snapshot and memory.")
    return population


async def commission(args):
    # Provider credentials stay in the target server. Only its private controller
    # token is loaded here; SDKProcess strips it from browser/model subprocesses.
    private = dotenv_values(args.target_env)
    control = private.get("TARGET_CONTROL_TOKEN") or os.environ.get("TARGET_CONTROL_TOKEN")
    if not control:
        raise ValueError("Target controller token is required.")
    os.environ["TARGET_CONTROL_TOKEN"] = control
    run_id = uuid4().hex
    evidence = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")).resolve()
    os.environ["TRYFOLK_EVIDENCE_ROOT"] = str(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    output = evidence / f"{run_id}-dodo-commissioning.json"
    result = {"schema": "tryfolk.dodo-commissioning.v1", "run_id": run_id,
              "diagnostic": True, "not_in_population_denominator": True,
              "execution_ready_declared": False, "started_at": utc_now(), "requested": args.count,
              "status": "starting", "journeys": [], "blockers": []}
    result["adapter_files_before"] = adapter_hashes()
    result["installed_sdk_versions"] = await installed_sdks()
    save(output, result)
    runtime = None
    try:
        readiness = await DodoTarget(args.origin).registration()
        result["readiness"] = readiness
        save(output, result)
        if readiness.get("ready") is not True or readiness.get("errors"):
            result["blockers"] = readiness.get("errors") or [{"code": "target_not_ready"}]
            raise ValueError("Actual Dodo target readiness must pass before commissioning.")
        manifest = readiness["manifest"]
        run = {"id": run_id, "population_id": uuid4().hex,
               "pass": args.pass_name, "persona_count": args.count, "provider": "dodo",
               "app_url": args.origin, "preview_url": args.origin,
               "revision": manifest["target_revision"], "source_commit": manifest["source_commit"],
               "source_digest": manifest["source_sha256"], "target_mode": manifest["mode"],
               "fault": manifest.get("fault") or "healthy", "phase": "running",
               "diagnostic": True, "not_in_population_denominator": True}
        result["run"] = run
        runtime = Runtime(SimpleNamespace(settings=SimpleNamespace(bulk_execution=True, browser_concurrency=4)))
        # This diagnostic has no product run in the store. Cancellation is owned
        # by the runner's task lifecycle; provider truth remains in Runtime.
        runtime.cancelled = lambda ignored_run_id: False
        fixtures = {}
        real_provision = runtime.provision

        async def capture_provision(bound_run, persona):
            fixture = await real_provision(bound_run, persona)
            fixtures[persona["id"]] = fixture
            # Preserve the genuine owned login token privately for a read-only
            # diagnostic if the browser transport closes. Never export it.
            save(evidence / f"{run_id}-{persona['id']}-owned-fixture.json", fixture)
            return fixture

        runtime.provision = capture_provision
        if args.discover and not args.population.exists():
            brief = await runtime.discover(run)
            args.brief = evidence / f"{run_id}-dodo-observed-brief.json"
            save(args.brief, brief)
            result["observed_brief_file"] = str(args.brief)
            save(output, result)
        population = await population_at(args, run_id)
        cohort_hash = digest(population)
        run["population_id"] = digest({"population": cohort_hash})[:32]
        result.update(population_file=str(args.population.resolve()), population_sha256=cohort_hash)
        if args.baseline:
            baseline = json.loads(args.baseline.read_text())
            if baseline.get("population_sha256") != cohort_hash:
                raise ValueError("Repaired commissioning requires the identical saved population.")
            result["baseline_run_id"] = baseline["run_id"]
            result["identical_saved_population"] = True
        ready_count = 0
        all_ready = asyncio.Event()
        release = asyncio.Event()
        events_path = evidence / f"{run_id}-dodo-commissioning-events.jsonl"
        event_log = events_path.open("a")
        events_path.chmod(0o600)

        async def emit(kind, data, persona_id):
            value = redact_payment(data)
            event_log.write(json.dumps({"at": utc_now(), "kind": kind, "persona_id": persona_id,
                                        "data": value}, default=str) + "\n")
            event_log.flush()
            runtime.telemetry.event(kind, value, persona_id)
            print(json.dumps({"kind": kind, "persona_id": persona_id}), flush=True)

        async def journey(persona):
            nonlocal ready_count
            ready_count += 1
            if ready_count == args.count:
                all_ready.set()
            await release.wait()
            started = time.monotonic()
            item = {"persona_id": persona["id"], "initial_state_sha256": digest(persona),
                    "started_at": utc_now()}
            with runtime.telemetry.persona(persona["id"]):
                try:
                    outcome = await runtime.journey(run, deepcopy(persona), lambda: False, emit)
                    item["outcome"] = redact_payment(outcome)
                    await emit("verification", outcome, persona["id"])
                except Exception as error:
                    item["error"] = {"type": type(error).__name__, "sdk_code": getattr(error, "sdk_code", None)}
                    # Keep native-gate evidence unchanged. Never classify a
                    # transport or safety gate as a passing/failed payment.
                    gate = getattr(error, "pre_submission_evidence", None)
                    if isinstance(gate, dict):
                        item["error"]["gate"] = gate
                    item["error"]["message"] = str(error)[:1500]
                    if persona["id"] in fixtures:
                        try:
                            oracle = await runtime.verification(run, fixtures[persona["id"]])
                            oracle_path = evidence / f"{run_id}-{persona['id']}-post-error-oracle.json"
                            save(oracle_path, {"source": "actual_independent_post_error_read_only_oracle",
                                "no_payment_resubmission": True, "no_order_or_entitlement_mutation": True,
                                "sanitized_provider_receipt_evidence_appended": True,
                                "outcome": redact_payment(oracle)})
                            item["post_error_oracle_file"] = str(oracle_path)
                            # Keep the journey unresolved. An independent receipt
                            # cannot establish the browser capability journey.
                        except Exception as oracle_error:
                            item["post_error_oracle_error"] = type(oracle_error).__name__
            item.update(finished_at=utc_now(), elapsed_seconds=round(time.monotonic() - started, 3),
                        trace_url=runtime.telemetry.persona_url(persona["id"]))
            result["journeys"].append(item)
            save(output, result)

        try:
            with runtime.telemetry.run(run):
                tasks = [asyncio.create_task(journey(persona)) for persona in population[:args.count]]
                await all_ready.wait()
                result["released_together_at"] = utc_now()
                release.set()
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            event_log.close()
        result["trace_readbacks"] = [await read_trace(run_id, trace_id)
                                     for trace_id in runtime.telemetry.persona_traces.values()]
        result["measured"] = sum(item.get("outcome", {}).get("measured") is True
                                 for item in result["journeys"])
        result["status"] = "journeys_recorded" if result["measured"] == args.count else "unresolved"
    except Exception as error:
        result["status"] = "blocked"
        result["error"] = {"type": type(error).__name__, "sdk_code": getattr(error, "sdk_code", None),
                           "message": str(error)[:1500], "traceback": traceback.format_exc(limit=12)}
    finally:
        if runtime:
            await runtime.close_run(run_id)
        result["adapter_files_after"] = adapter_hashes()
        result["adapter_sources_unchanged"] = result["adapter_files_before"] == result["adapter_files_after"]
        events_path = evidence / f"{run_id}-dodo-commissioning-events.jsonl"
        if events_path.exists():
            result["events_file"] = str(events_path)
            result["events_sha256"] = hashlib.sha256(events_path.read_bytes()).hexdigest()
        result["finished_at"] = utc_now()
        save(output, result)
    print(json.dumps({"evidence": str(output), "status": result["status"],
                      "measured": result.get("measured", 0), "requested": args.count}), flush=True)
    return 0 if result["status"] == "journeys_recorded" else 1


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:3301")
    parser.add_argument("--count", type=int, choices=(1, 4), default=1)
    parser.add_argument("--population", type=Path, default=Path("runtime/dodo-commissioning-population.json"))
    parser.add_argument("--brief", type=Path, help="Actual generation brief, used only when population file is absent")
    parser.add_argument("--discover", action="store_true", help="Run real BrowserUse discovery before generating four")
    parser.add_argument("--target-env", type=Path, default=Path("dodo-target/.env.local"))
    parser.add_argument("--pass-name", choices=("before", "after"), default="before")
    parser.add_argument("--baseline", type=Path, help="Previous commissioning evidence; requires the same cohort hash")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(commission(arguments())))
