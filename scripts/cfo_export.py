"""Import a finalized owner-inspected CFO snapshot, preserving evidence and USD units.

This does not export a native Page, authenticate OAuth, start a trial, or call a model.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from tryfolk.budget import validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Finalized inspected JSON contract v2")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--plan-id", required=True)
    parser.add_argument("--evidence", type=Path, action="append", required=True,
                        help="Actual inspected evidence file; repeat for multiple sources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = args.snapshot.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise ValueError("New imports require a finalized inspected JSON forecast contract v2.")
        if value.get("source_url") != args.source_url or value.get("plan_id") != args.plan_id:
            raise ValueError("Plan ID and source URL must match the inspected snapshot; overrides are forbidden.")
        value["snapshot_sha256"] = hashlib.sha256(raw).hexdigest()
        validate_plan(value)
        supplied_hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in args.evidence}
        required_hashes = {
            value["inspection_evidence_sha256"], value["native_ledger_snapshot"]["evidence_sha256"],
            *(p["evidence_sha256"] for p in value["assumption_provenance"].values()),
        }
        if value.get("operator_allocation"):
            required_hashes.update(value["operator_allocation"][k] for k in
                                   ("evidence_sha256", "native_forecast_sha256"))
        if not required_hashes <= supplied_hashes:
            raise ValueError("Inspected evidence files must match all forecast and native ledger evidence hashes.")
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    # Preserve the last accepted contract on any rejected or interrupted import.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, args.output)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    print(json.dumps({
        "status": "imported_inspected_forecast", "plan_id": args.plan_id,
        "source": value["source"], "snapshot_sha256": value["snapshot_sha256"],
        "contingency_basis_points": value["assumptions"]["contingency_basis_points"],
        "quote_budget_microusd": value["assumptions"]["quote_budget_microusd"],
        "lifetime_budget_microusd": value["assumptions"]["lifetime_budget_microusd"],
        "runtime_reloaded": False, "reservations_changed": False,
    }))


if __name__ == "__main__":
    main()
