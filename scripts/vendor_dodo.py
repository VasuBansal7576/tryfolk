"""Export the licensed upstream tree through the mandated read-only gh-axi API."""

import base64
from pathlib import Path
import subprocess

REVISION = "ef7cbd980d8f1e57b036dc2af71ff05521b6175a"
ROOT = Path("owned-target/upstream")


if __name__ == "__main__":
    tree_result = subprocess.run(
        [
            "gh-axi",
            "api",
            f"repos/dodopayments/dodo-checkout-demo/git/trees/{REVISION}?recursive=1",
            "--full",
            "--jq",
            '.tree[] | select(.type == "blob") | [.path,.sha] | @tsv',
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in tree_result.stdout.splitlines():
        path, sha = line.split("\t")
        if path.startswith("public/"):
            continue
        if (ROOT / path).exists():
            continue
        result = subprocess.run(
            [
                "gh-axi",
                "api",
                f"repos/dodopayments/dodo-checkout-demo/git/blobs/{sha}",
                "--full",
                "--jq",
                ".content",
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        output = ROOT / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(base64.b64decode(result.stdout))
    (ROOT.parent / "PROVENANCE.md").write_text(
        f"# Owned Dodo sandbox target\n\nUpstream: https://github.com/dodopayments/dodo-checkout-demo\nPinned commit: {REVISION}\nLicense: MIT; upstream LICENSE.md retained. Exported with read-only gh-axi API. Upstream source is unchanged. Large upstream public image/font assets are excluded from this source export; use the upstream repository for the original full visual demo. Local fixture runtime is separately labelled, not a Dodo purchase.\n"
    )
