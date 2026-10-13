"""Closed read-only OS and persisted startup facts for an actual owned run.

This records worker isolation, not checkout transport success or overlap.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess

from tryfolk.process import ROOT


def capture(run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Exact owned run ID required")
    with sqlite3.connect("file:" + str(ROOT / "runtime/tryfolk.sqlite") + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("Actual owned run is absent")
        run = json.loads(row[0])
        startup = [{"persona_id": p, "created_at": t}
                   for p, t in db.execute("SELECT persona_id,created_at FROM events "
                                          "WHERE run_id=? AND kind='persona_initialized'", (run_id,))]
    raw = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True)
    processes = {}
    for line in raw.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            processes[int(parts[0])] = (int(parts[1]), parts[2])
    adapters = []
    for pid, (parent, command) in processes.items():
        sdk = next((name for name in ("stagehand", "tinytroupe")
                    if str(ROOT / "integrations" / name / "adapter.py") in command), None)
        if sdk is None:
            continue
        result = subprocess.run(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        cwd = next((line[1:] for line in result.stdout.splitlines() if line.startswith("n")), None)
        owned_cwd = bool(cwd and Path(cwd).parent == ROOT / "runtime/sdk"
                         and re.fullmatch(sdk + r"-[a-f0-9]{32}", Path(cwd).name))
        descendants = {pid}
        while True:
            expanded = descendants | {p for p, (ppid, _) in processes.items() if ppid in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        browsers = []
        for child in descendants - {pid}:
            args = processes[child][1]
            profile = re.search(r"--user-data-dir=([^ ]*stagehand-profile-[^ ]*)", args)
            port = re.search(r"--remote-debugging-port=(\d+)", args)
            if profile and port:
                browsers.append({"pid": child, "cdp_port": int(port[1]),
                                 "owned_profile_sha256": hashlib.sha256(profile[1].encode()).hexdigest()})
        adapters.append({"sdk": sdk, "pid": pid, "parent_pid": parent,
                         "owned_private_workspace": owned_cwd,
                         "workspace_sha256": hashlib.sha256(cwd.encode()).hexdigest() if owned_cwd else None,
                         "browsers": browsers})
    counts = {sdk: sum(x["sdk"] == sdk for x in adapters) for sdk in ("stagehand", "tinytroupe")}
    result = {"run_id": run_id, "observed_at": datetime.now(timezone.utc).isoformat(),
              "phase": run["phase"], "requested": run["persona_count"],
              "source": "actual_read_only_local_OS_and_persisted_startup_observation",
              "no_provider_requests": True, "no_database_mutations": True,
              "process_binding_limitation": "OS adapter arguments do not contain run or persona IDs; concurrent unrelated SDK workers would invalidate inference.",
              "persona_startup_events": startup, "sdk_counts": counts, "adapters": adapters,
              "checkout_overlap_claimed": False}
    path = ROOT / "runtime/evidence" / (run_id + "-worker-isolation-readiness.json")
    path.write_text(json.dumps(result, indent=2) + "\n")
    path.chmod(0o600)
    return {"evidence": str(path), "sdk_counts": counts, "persona_startups": len(startup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    print(json.dumps(capture(parser.parse_args().run_id)))
