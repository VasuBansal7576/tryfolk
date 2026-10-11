"""Invented JSONL peers for local startup tests; no SDK, model or browser calls."""

import json
from pathlib import Path
import signal
import sys
import time

role, directory = sys.argv[1:]
root = Path(directory)
settings = json.loads((root / "settings.json").read_text())


def stopped(*_):
    (root / f"{role}.closed").touch()
    sys.exit(0)


signal.signal(signal.SIGTERM, stopped)
for line in sys.stdin:
    command = json.loads(line)
    operation = command["op"]
    with (root / f"{role}.commands.jsonl").open("a") as output:
        output.write(json.dumps(command) + "\n")
    if operation in ("init", "goto"):
        (root / f"{role}.started").touch()
        if settings.get("require_overlap"):
            peer = "stagehand" if role == "tinytroupe" else "tinytroupe"
            deadline = time.monotonic() + 0.5
            while not (root / f"{peer}.started").exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            if not (root / f"{peer}.started").exists():
                print(json.dumps({"ok": False, "error": "independent peer did not start"}), flush=True)
                continue
        if settings.get("failed_role") == role:
            if settings.get("wait_for_peer_ready_before_failure"):
                deadline = time.monotonic() + 0.5
                while not (root / f"{peer}.ready").exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert (root / f"{peer}.ready").exists()
            print(json.dumps({"ok": False, "error": "fixture startup failed"}), flush=True)
            continue
        time.sleep(settings.get(f"{role}_delay", 0))
        (root / f"{role}.ready").touch()
        data = {"fixture": True, "restored_persona": command.get("persona")}
    elif operation == "observe":
        assert all((root / f"{peer}.ready").exists() for peer in ("tinytroupe", "stagehand"))
        data = {"url": "http://127.0.0.1:3100", "text": "Fixture only", "actions": []}
    elif operation == "decide":
        assert all((root / f"{peer}.ready").exists() for peer in ("tinytroupe", "stagehand"))
        data = {"intent": {"type": "FINISH"}}
    elif operation == "snapshot":
        if settings.get("failed_snapshot"):
            print(json.dumps({"ok": False, "error": "fixture snapshot failed"}), flush=True)
            continue
        data = {"fixture": True, "snapshot": "retained fixture memory"}
    else:
        data = {"fixture": True}
    print(json.dumps({"ok": True, "data": data}), flush=True)
