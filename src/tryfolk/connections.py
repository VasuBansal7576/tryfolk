"""Owner activation of approved Dodo builds, separate from execution authority.

The private Settings.targets registry authorizes the source path and exact origin.
A browser-supplied URL never authorizes new filesystem or network access.
"""

import hashlib
from itertools import chain
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit

import httpx

from .browser_policy import BrowserPolicy, DODO_CHECKOUT
from .budget import load_plan
from .service import DomainError
from .target_provenance import dodo_provenance

SETUP_CODES = frozenset(
    {
        "sandbox_disabled",
        "unsafe_origin",
        "missing_control_token",
        "weak_control_token",
        "missing_write_key",
        "missing_read_key",
        "keys_not_distinct",
        "key_scope_unconfirmed",
        "missing_product",
        "missing_amount",
        "missing_currency",
        "invalid_mode",
        "provider_override_forbidden",
        "build_revision_mismatch",
        "build_manifest_missing",
    }
)

# Same content definition as the owned demo's sourceFingerprint, evaluated by
# an independent, fixed built-in-only reader. No application code is executed.
SOURCE_HASH = """
import {createHash} from 'node:crypto';
import {existsSync,readFileSync,readdirSync} from 'node:fs';
const h=createHash('sha256');
function walk(dir){
 if(!existsSync(dir))return;
 for(const e of readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
  const n=dir+'/'+e.name;
  if(e.isDirectory())walk(n);
  else if(e.isFile())h.update(n).update('\\0').update(readFileSync(n));
 }
}
for(const d of ['app','lib','scripts'])walk(d);
for(const n of ['package.json','package-lock.json','next.config.mjs','tsconfig.json'])
 if(existsSync(n))h.update(n).update('\\0').update(readFileSync(n));
process.stdout.write(h.digest('hex'));
"""


def gate(code, message, action):
    return {"code": code, "message": message, "action": action}


def provenance_error():
    return DomainError(
        422,
        "connection_provenance",
        "The approved source and target build could not be verified.",
        [
            gate(
                "connection_provenance",
                "Source, license, manifest or registry pins differ.",
                "Prepare a clean owned Dodo build and update its exact private registry pins, then register again.",
            ),
        ],
    )


def origin(url):
    try:
        parsed = urlsplit(url)
        result = f"{parsed.scheme}://{parsed.netloc}"
        if url not in (result, result + "/"):
            raise ValueError
        BrowserPolicy(result, "dodo")
        return result
    except (ValueError, TypeError):
        raise DomainError(
            422, "unsafe_origin", "Use an approved exact HTTP 127.0.0.1 origin on ports 3300-3399."
        ) from None


def registration_key(target):
    # Include the private authorization identity, never expose the source path.
    identity = [
        target.url,
        target.repo_path,
        target.source_commit,
        target.source_digest,
        target.revision,
        target.mode,
        target.fault,
        target.allowed_origins,
        target.target_provenance,
    ]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def command(args, source):
    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    return subprocess.run(
        args, cwd=source, env=env, check=True, capture_output=True, text=True, timeout=5
    ).stdout.strip()


def verify_source(target):
    if (
        target.provider != "dodo"
        or not target.repo_path
        or not Path(target.repo_path).is_absolute()
        or list(target.allowed_origins) != [target.url]
        or target.mode not in ("healthy", "broken")
        or not re.fullmatch(r"[a-f0-9]{40}", target.source_commit or "")
        or not re.fullmatch(r"[a-f0-9]{64}", target.source_digest or "")
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", target.revision or "")
        or not isinstance(target.target_provenance, dict)
    ):
        raise ValueError("Missing explicit source authorization")
    source = Path(target.repo_path)
    if source.resolve() != source or not source.is_dir():
        raise ValueError("Source must be a real approved directory")
    total = 0
    files = []
    entries = 0
    roots = [
        source / name
        for name in (
            "app",
            "lib",
            "scripts",
            "package.json",
            "package-lock.json",
            "next.config.mjs",
            "tsconfig.json",
            "LICENSE.md",
        )
    ]
    for root in roots:
        paths = chain([root], root.rglob("*")) if root.is_dir() and not root.is_symlink() else [root]
        for path in paths:
            entries += 1
            if entries > 2048:
                raise ValueError("Source exceeds this adapter's bound")
            if path.is_symlink():
                raise ValueError("Symlink source is unsupported")
            if path.is_file():
                files.append(path.relative_to(source).as_posix())
                total += path.stat().st_size
                if total > 10_000_000:
                    raise ValueError("Source exceeds this adapter's bound")
    git = ["git", "--literal-pathspecs", "-c", "core.fsmonitor=false"]
    repo = Path(command(git + ["rev-parse", "--show-toplevel"], source)).resolve()
    if not source.is_relative_to(repo):
        raise ValueError("Source is outside its Git tree")
    command(git + ["ls-files", "--error-unmatch", "--", *files], source)
    if command(
        git + ["status", "--porcelain", "--untracked-files=all", "--ignore-submodules=all", "--", "."], source
    ):
        raise ValueError("Source is not clean")
    command(git + ["cat-file", "-e", target.source_commit + "^{commit}"], source)
    command(
        git + ["diff", "--exit-code", "--no-ext-diff", "--no-textconv", target.source_commit, "--", "."],
        source,
    )
    if command(["node", "--input-type=module", "-e", SOURCE_HASH], source) != target.source_digest:
        raise ValueError("Source content digest mismatch")
    return source


def readiness(target):
    # Only called after registry selection and source verification. No controller
    # or provider secret is needed for this public, read-only owned contract.
    deadline = time.monotonic() + 5
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=3) as client:
        with client.stream("GET", target.url + "/api/target/readiness") as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic() > deadline:
                    raise httpx.ReadTimeout("Owned target contract deadline exceeded")
                data.extend(chunk)
                if len(data) > 256_000:
                    raise ValueError("Target contract exceeds bound")
    return json.loads(data)


class Connections:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        with store.transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS app_connections(
                registry_key TEXT PRIMARY KEY, app_url TEXT NOT NULL, payload TEXT NOT NULL)""")

    def candidate(self, target):
        return {
            "id": "connection_" + registration_key(target)[:24],
            "app_url": target.url,
            "registration_status": "unregistered",
            "execution_ready": False,
            "provider": target.provider,
            "revision": target.revision,
            "source": {
                "status": "pending",
                "repository_label": "Owned Dodo checkout demo",
                "repository_url": None,
                "commit": None,
                "digest": None,
                "detail": "The server-approved build has not been independently registered.",
            },
            "gates": [
                gate(
                    "connection_required",
                    "This approved build has not been registered.",
                    "Sign in as owner and register the configured app URL.",
                )
            ],
        }

    def list(self):
        targets = []
        with self.store.transaction() as db:
            saved = {
                row["registry_key"]: json.loads(row["payload"])
                for row in db.execute("SELECT registry_key,payload FROM app_connections")
            }
        for target in self.settings.targets:
            if target.provider != "dodo":
                continue
            snapshot = saved.get(registration_key(target))
            item = snapshot["connection"] if snapshot else self.candidate(target)
            if item["registration_status"] == "registered":
                item["gates"] = self.execution_gates(target, snapshot["setup_ready"], snapshot["setup_codes"])
                item["execution_ready"] = target.execution_ready is True and not item["gates"]
                try:
                    verify_source(target)
                except (ValueError, TypeError, OSError, subprocess.SubprocessError):
                    item["source"]["status"] = "stale"
                    item["source"]["detail"] = (
                        "Retained registration pins no longer match the approved local source."
                    )
                    item["execution_ready"] = False
                    item["gates"].insert(
                        0,
                        gate(
                            "connection_source_stale",
                            "The registered source has changed or is unavailable.",
                            "Restore the exact approved source, or approve and register the new build pins.",
                        ),
                    )
            targets.append(item)
        return {
            "version": "1",
            "registration_supported": True,
            "ownership_scope": "owner-approved-loopback-dodo-test",
            "supported_adapters": ["owned-dodo-special-downloads-v1"],
            "targets": targets,
        }

    def execution_gates(self, target, setup_ready, setup_codes):
        gates = list(self.settings.gates) + list(target.setup_gates)
        for code in setup_codes:
            gates.append(
                gate(
                    code,
                    "The owned target reports an outstanding setup requirement.",
                    "Resolve this code using the owned target setup contract; keep credentials server-side.",
                )
            )
        if not setup_ready and not setup_codes:
            gates.append(
                gate(
                    "target_not_ready",
                    "The owned target has not declared setup ready.",
                    "Inspect the approved app readiness contract before execution.",
                )
            )
        if target.execution_ready is not True:
            gates.append(
                gate(
                    "dodo_runtime_validation",
                    "Independent provider and browser execution validation is outstanding.",
                    "Retain source registration; complete the authorized owned Test adapter validation separately.",
                )
            )
        if not self.settings.cfo_plan_path or not self.settings.cfo_plan_path.is_file():
            gates.append(
                gate(
                    "cfo_plan_missing",
                    "The official CFO business plan has not been imported.",
                    "Import the owner-created official business plan with before and after assumptions.",
                )
            )
        else:
            try:
                load_plan(self.settings.cfo_plan_path)
            except (ValueError, KeyError, TypeError, OSError):
                gates.append(
                    gate(
                        "cfo_plan_invalid",
                        "The configured CFO plan did not pass its provenance contract.",
                        "Import and validate the inspected official plan; file presence does not establish readiness.",
                    )
                )
        return gates

    def register(self, url):
        app_origin = origin(url)
        matches = [t for t in self.settings.targets if t.url == app_origin and t.provider == "dodo"]
        if len(matches) != 1:
            raise DomainError(
                422,
                "connection_unsupported",
                "An owner must approve this compatible target and its source in the private server registry.",
                [
                    gate(
                        "connection_unsupported",
                        "No unique supported owner-approved target matches this URL.",
                        "Configure the licensed owned Dodo adapter and exact source pins on the server first.",
                    ),
                ],
            )
        target = matches[0]
        try:
            source = verify_source(target)
        except (ValueError, TypeError, OSError, subprocess.SubprocessError):
            raise provenance_error() from None
        try:
            contract = readiness(target)
        except httpx.HTTPError:
            raise DomainError(
                503,
                "connection_target_unavailable",
                "The approved target contract is unavailable.",
                [
                    gate(
                        "connection_target_unavailable",
                        "The approved local app did not return a readable contract.",
                        "Start the approved local target, check its readiness endpoint, then register again.",
                    ),
                ],
            ) from None
        except (ValueError, TypeError):
            raise provenance_error() from None
        try:
            manifest = contract["manifest"]
            provenance = dodo_provenance(manifest, source)
            if (
                provenance != target.target_provenance
                or manifest["source_commit"] != target.source_commit
                or manifest["source_sha256"] != target.source_digest
                or manifest["target_revision"] != target.revision
                or manifest["mode"] != target.mode
                or manifest.get("contract_version") != "1"
                or manifest.get("app_origin") != target.url
                or manifest.get("safe_origins") != [target.url]
                or manifest.get("checkout_origins") != [DODO_CHECKOUT]
                or manifest.get("adapter") != "persistent-local-sqlite"
                or manifest.get("fixture_auth_enabled") is not True
                or manifest.get("product", {}).get("type") != "one-time"
                or contract.get("evidence_source") != "dodo_test_api"
                or type(contract.get("ready")) is not bool
                or not isinstance(contract.get("errors"), list)
            ):
                raise ValueError("Contract differs from approved adapter")
        except (KeyError, TypeError, ValueError, AttributeError, OSError):
            raise provenance_error() from None
        item = self.candidate(target)
        item["registration_status"] = "registered"
        item["source"] = {
            "status": "verified",
            "repository_label": "Owned Dodo checkout demo",
            "repository_url": provenance["upstream_url"],
            "commit": target.source_commit,
            "digest": target.source_digest,
            "detail": "Owned source, retained MIT license and exact served build verified. Payment execution is separate.",
        }
        setup_codes = []
        for error in contract["errors"]:
            code = error.get("code") if isinstance(error, dict) else None
            if not isinstance(code, str) or code not in SETUP_CODES:
                raise provenance_error()
            setup_codes.append(code)
        gates = self.execution_gates(target, contract["ready"], setup_codes)
        item["gates"] = gates
        item["execution_ready"] = target.execution_ready is True and not gates
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO app_connections VALUES (?,?,?)",
                (
                    registration_key(target),
                    target.url,
                    json.dumps(
                        {
                            "connection": item,
                            "setup_ready": contract["ready"],
                            "setup_codes": setup_codes,
                        }
                    ),
                ),
            )
        return item
