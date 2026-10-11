"""Register an exact licensed owned Dodo build with actionable readiness gates."""

import argparse
import asyncio
import json
from pathlib import Path
from tryfolk.dodo import DodoTarget
from tryfolk.target_provenance import dodo_provenance
from tryfolk.dodo_runtime_validation import execution_ready


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default="http://127.0.0.1:3300")
    args = parser.parse_args()
    readiness = await DodoTarget(args.origin).registration()
    m = readiness["manifest"]
    path = Path("runtime/connections.json")
    existing = json.loads(path.read_text()) if path.exists() else []
    target = {
        "url": args.origin,
        "revision": m["target_revision"],
        "fault": m["fault"] or "healthy",
        "provider": "dodo",
        "repo_path": str(Path("dodo-target").resolve()),
        "allowed_origins": m["safe_origins"],
        "source_commit": m["source_commit"], "source_digest": m["source_sha256"], "mode": m["mode"],
        "target_provenance": dodo_provenance(m, Path("dodo-target").resolve()),
        "execution_ready": execution_ready(m, Path("runtime/dodo-execution-validation.json"), Path(__file__).resolve().parents[1]),
        "setup_gates": [
            {
                "code": "dodo_target_" + error["code"],
                "message": error["code"].replace("_", " "),
                "action": error["action"],
            }
            for error in readiness["errors"]
        ],
    }
    path.write_text(json.dumps([x for x in existing if x["url"] != args.origin] + [target], indent=2))
    evidence = Path("runtime/dodo-readiness.json")
    evidence.write_text(json.dumps(readiness, indent=2))
    print(
        json.dumps(
            {
                "registered": args.origin,
                "revision": m["target_revision"],
                "ready": readiness["ready"],
                "provider_baseline_verified": readiness["provider_baseline_verified"],
                "errors": readiness["errors"],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
