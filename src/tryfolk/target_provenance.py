"""Frozen disclosure from the registered build and retained official MIT license."""
import hashlib
from pathlib import Path
import re

UPSTREAM = "ef7cbd980d8f1e57b036dc2af71ff05521b6175a"
LICENSE_SHA = "3546b8d22c1657e8f6d36ae83e8721f894946337d9966e1d84fbfcdfa1c9ae0b"


def dodo_provenance(manifest, source):
    license_file = Path(source) / "LICENSE.md"
    if (
        license_file.is_symlink() or not license_file.is_file()
        or hashlib.sha256(license_file.read_bytes()).hexdigest() != LICENSE_SHA
        or manifest.get("target_id") != "tryfolk-owned-dodo-special-downloads"
        or manifest.get("upstream_commit") != UPSTREAM
        or manifest.get("provider_environment") != "test_mode"
        or manifest.get("source_clean") is not True
        or not re.fullmatch(r"[a-f0-9]{40}", manifest.get("source_commit") or "")
        or not re.fullmatch(r"[a-f0-9]{64}", manifest.get("source_sha256") or "")
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", manifest.get("target_revision") or "")
    ):
        raise ValueError("Owned target provenance requires the actual pinned build and retained MIT license.")
    mode, repair, fault = manifest.get("mode"), manifest.get("repair", {}), manifest.get("fault")
    if (
        mode not in ("healthy", "broken") or repair.get("source_file") != "lib/fulfillment-policy.ts"
        or repair.get("disposition") not in ("granted", "suppressed")
        or (repair["disposition"] == "suppressed") != (fault == "payment-settled-fulfillment-suppressed")
        or (mode == "healthy" and repair["disposition"] != "granted")
    ):
        raise ValueError("Controlled fault disclosure does not match the pinned fulfillment manifest.")
    return {
        "target_id":manifest["target_id"], "ownership":"owned_sandbox", "license":"MIT",
        "license_sha256":LICENSE_SHA, "upstream_commit":UPSTREAM,
        "upstream_url":"https://github.com/dodopayments/dodo-checkout-demo/tree/"+UPSTREAM,
        "source_commit":manifest["source_commit"], "source_digest":manifest["source_sha256"],
        "baseline_target_revision":manifest["target_revision"], "baseline_mode":mode,
        "baseline_fulfillment_disposition":repair["disposition"],
        "controlled_fulfillment_defect":repair["disposition"] == "suppressed", "sandbox":"dodo_test_mode",
    }
