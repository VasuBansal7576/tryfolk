import asyncio
import os
from pathlib import Path
import json
from dotenv import load_dotenv, dotenv_values
from tryfolk.config import Settings
from tryfolk.service import Service
from tryfolk.runtime import Runtime
from tryfolk.worker import Worker

if __name__ == "__main__":
    import fcntl

    lock = Path("runtime/worker.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    keys = json.loads(Path("runtime/local-secrets.json").read_text())
    os.environ["TARGET_FIXTURE_SECRET"] = keys["fixture"]
    os.environ["TRYFOLK_OWNER_TOKEN"] = keys["owner"]
    # The orchestration worker needs only the scoped local controller capability.
    # Actual provider keys remain in the target server, outside model processes.
    target_private = dotenv_values(Path("dodo-target/.env.local"))
    if target_private.get("TARGET_CONTROL_TOKEN"):
        os.environ["TARGET_CONTROL_TOKEN"] = target_private["TARGET_CONTROL_TOKEN"]
    if os.environ.get("TRYFOLK_NEATLOGS_ENV"):
        load_dotenv(os.environ["TRYFOLK_NEATLOGS_ENV"])
    service = Service(Settings.from_env())
    asyncio.run(Worker(service, Runtime(service)).serve())
