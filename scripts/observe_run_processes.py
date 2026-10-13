"""Observe an owned run and SDK process overlap without executing its journeys."""
import argparse
from datetime import datetime, timezone
import json
import re
import sqlite3
import subprocess
import time

from tryfolk.process import ROOT


def observe(run_id, seconds):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise ValueError("Exact owned run ID required")
    deadline = time.monotonic() + seconds
    path = ROOT / "runtime/evidence" / f"{run_id}-sdk-process-observations.jsonl"
    if path.is_symlink():
        raise ValueError("Evidence output must not be a symlink")
    last = None
    while True:
        db = sqlite3.connect("file:" + str(ROOT / "runtime/tryfolk.sqlite") + "?mode=ro", uri=True)
        try:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        finally:
            db.close()
        if not row:
            raise ValueError("Run absent from owned store")
        run = json.loads(row[0])
        processes = []
        for line in subprocess.check_output(["ps", "-axo", "pid=,ppid=,lstart=,command="], text=True).splitlines():
            for sdk in ("tinytroupe", "stagehand"):
                owned = str(ROOT / "integrations" / sdk / "adapter.py")
                if owned in line:
                    parts = line.strip().split(None, 7)
                    if len(parts) >= 8 and parts[0].isdigit():
                        processes.append({"sdk": sdk, "pid": int(parts[0]), "parent_pid": int(parts[1]),
                                          "os_start": " ".join(parts[2:7]), "observed_owned_adapter_path": owned})
        counts = {sdk: sum(p["sdk"] == sdk for p in processes) for sdk in ("tinytroupe", "stagehand")}
        current = (run["phase"], counts["tinytroupe"], counts["stagehand"])
        if current != last or time.monotonic() >= deadline:
            item = {"source": "actual_local_ps_observation", "observed_at": datetime.now(timezone.utc).isoformat(),
                    "requested_run_id": run_id, "recorded_run_phase": run["phase"],
                    "run_binding": "process_arguments_do_not_embed_run_or_persona_id", "processes": processes}
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as stream:
                stream.write(json.dumps(item) + "\n")
            path.chmod(0o600)
            last = current
        terminal = run["phase"] in {"completed", "blocked", "failed", "cancelled"}
        all_lanes = run["phase"] == "running" and all(n >= run["persona_count"] for n in counts.values())
        if terminal or all_lanes or time.monotonic() >= deadline:
            print(json.dumps({"phase": run["phase"], **counts, "outcomes": len(run.get("outcomes", {})),
                              "process_binding_limitation": "No run/persona IDs embedded in process arguments"}))
            return
        time.sleep(4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--seconds", type=int, choices=range(1, 46), default=40)
    args = parser.parse_args()
    observe(args.run_id, args.seconds)


if __name__ == "__main__":
    main()
