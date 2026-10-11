"""Copy licensed owned target source into an isolated repair repository.

No worktree administration. No credentials, build, database or upstream app copy.
Only the fulfillment function may change; independent payment verification stays pinned.
"""

import json
from pathlib import Path
import re
import shutil
import subprocess
from .codex_bridge import clean_env

SOURCE_FILE = "lib/fulfillment-policy.ts"
FINGERPRINT_SCRIPT = r"""
import {createHash} from 'node:crypto';
import {existsSync,readFileSync,readdirSync,lstatSync} from 'node:fs';
import {join} from 'node:path';
const root=process.argv[2],hash=createHash('sha256');
function walk(relative) {
 const path=join(root,relative);if(!existsSync(path))return;
 if(lstatSync(path).isSymbolicLink())throw Error('Source directory symlink');
 for(const entry of readdirSync(path,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))) {
  const name=`${relative}/${entry.name}`;
  if(entry.isSymbolicLink())throw Error('Source symlink');
  if(entry.isDirectory())walk(name);
  else if(entry.isFile())hash.update(name).update('\0').update(readFileSync(join(root,name)));
 }
}
for(const dir of ['app','lib','scripts'])walk(dir);
for(const file of ['package.json','package-lock.json','next.config.mjs','tsconfig.json']) {
 const path=join(root,file);if(existsSync(path)) {
  if(lstatSync(path).isSymbolicLink())throw Error('Source symlink');
  hash.update(file).update('\0').update(readFileSync(path));
 }
}
console.log(hash.digest('hex'));
"""


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True, env=clean_env()
    ).stdout.strip()


def entire_installer_env(workspace):
    """Scope official hook installation to a validated standalone repair repo.

    Firstmate's command-scope hooksPath remains intact for ordinary Git/Codex.
    Only this installer's copy appends the exact private hooksPath, retaining
    every injected Git setting. No shared hook, global config or parent changes.
    """
    workspace = Path(workspace).resolve()
    private_git = workspace / ".git"
    env = clean_env()
    if any(env.get(key) for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_CONFIG_PARAMETERS")):
        raise ValueError("Entire installer forbids inherited Git root or parameter redirects.")
    if private_git.is_symlink() or not private_git.is_dir():
        raise ValueError("Entire installation requires an owned standalone Git directory.")

    def read(*args):
        result = subprocess.run(
            ["git", "-C", str(workspace), *args], env=env,
            check=True, capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip()

    if (
        Path(read("rev-parse", "--show-toplevel")).resolve() != workspace
        or Path(read("rev-parse", "--absolute-git-dir")).resolve() != private_git
        or (workspace / read("rev-parse", "--git-common-dir")).resolve() != private_git
    ):
        raise ValueError("Entire installation requires this exact standalone source repository.")
    hooks = private_git / "hooks"
    local_path = Path(read("config", "--local", "--get", "core.hooksPath"))
    if (workspace / local_path).resolve() != hooks or hooks.is_symlink():
        raise ValueError("Entire installer requires the repository's exact private hooks directory.")
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0"))
        if not 0 <= count <= 100:
            raise ValueError()
        for i in range(count):
            if f"GIT_CONFIG_KEY_{i}" not in env or f"GIT_CONFIG_VALUE_{i}" not in env:
                raise ValueError()
    except (ValueError, KeyError):
        raise ValueError("Invalid inherited Git config entries; installer cannot safely scope them.") from None
    env[f"GIT_CONFIG_KEY_{count}"] = "core.hooksPath"
    env[f"GIT_CONFIG_VALUE_{count}"] = str(hooks)
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    if Path(read("rev-parse", "--git-path", "hooks")).resolve() != hooks:
        raise ValueError("Effective Entire installer hooks path is not the exact private hooks directory.")
    return env


def source_fingerprint(root):
    result = subprocess.run(
        ["node", "--input-type=module", "-", str(Path(root).resolve())],
        input=FINGERPRINT_SCRIPT,
        capture_output=True,
        text=True,
        env=clean_env(),
        timeout=10,
    )
    if result.returncode or not re.fullmatch(r"[a-f0-9]{64}", result.stdout.strip()):
        raise ValueError("Owned source fingerprint failed; symlinks or invalid source are forbidden.")
    return result.stdout.strip()


class OwnedSource:
    def __init__(self, worktree):
        self.worktree = Path(worktree).resolve()
        if Path(git(self.worktree, "rev-parse", "--show-toplevel")).resolve() != self.worktree:
            raise ValueError("Source snapshot requires the exact authorized Git root.")
        self.target = self.worktree / "dodo-target"

    def snapshot(self, workspace, *, expected_digest, contract=None):
        workspace = Path(workspace)
        if workspace.exists():
            raise ValueError("An existing repair workspace cannot be overwritten.")
        if git(self.worktree, "status", "--porcelain", "--untracked-files=all", "--", "dodo-target"):
            raise ValueError("Owned target source must be clean before baseline snapshot.")
        files = git(self.worktree, "ls-files", "--", "dodo-target").splitlines()
        approved = []
        for relative in files:
            path = self.worktree / relative
            name = path.relative_to(self.target)
            if name.parts[0] == "upstream" or name.name.startswith(".env"):
                continue
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(self.target):
                raise ValueError("Tracked source must be a regular owned file without external symlinks.")
            approved.append((path, name))
        if contract is not None:
            if (
                contract.get("allowed_source_files") != [SOURCE_FILE]
                or contract.get("source_clean") is not True
                or contract.get("source_sha256") != expected_digest
            ):
                raise ValueError("Original owned source contract identity or one-file seam changed.")
            expected_files = {item["path"]: item["sha256"] for item in contract["export_files"]}
            import hashlib

            if expected_files != {
                str(name): hashlib.sha256(path.read_bytes()).hexdigest() for path, name in approved
            }:
                raise ValueError("Tracked export files differ from the original owned source contract.")
        if source_fingerprint(self.target) != expected_digest:
            raise ValueError("Source no longer matches the measured baseline manifest.")
        required = {str(name) for _, name in approved}
        if not {SOURCE_FILE, "SOURCE.md", "LICENSE.md"} <= required:
            raise ValueError("Owned runnable source and licensed provenance must be tracked.")
        workspace.mkdir(parents=True)
        for path, name in approved:
            destination = workspace / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        if source_fingerprint(workspace) != expected_digest:
            raise ValueError("Copied runnable source differs from the pinned baseline.")
        git(workspace, "init", "-b", "tryfolk-repair")
        # Inherited launcher hooks can be protected shared infrastructure. Entire
        # must install only in this new repository's privately owned hooks dir.
        git(workspace, "config", "core.hooksPath", str(workspace.resolve() / ".git/hooks"))
        (workspace / ".git/info/exclude").write_text(
            ".entire/\n.codex/\nnode_modules/\n.next/\n.target-manifest.json\n*.tsbuildinfo\n"
        )
        git(workspace, "add", ".")
        git(
            workspace,
            "-c",
            "user.name=Tryfolk Local",
            "-c",
            "user.email=local@tryfolk.invalid",
            "commit",
            "-m",
            "Pin owned Dodo baseline source and licensed provenance",
        )
        pin = {
            "base_commit": git(workspace, "rev-parse", "HEAD"),
            "original_source_commit": git(self.worktree, "rev-parse", "HEAD"),
            "source_digest": expected_digest,
            "allowed_file": SOURCE_FILE,
            "scope": "fulfillment_policy_only",
            "workspace": str(workspace.resolve()),
        }
        return pin

    def verify(self, workspace, pin, *, require_changed):
        workspace = Path(workspace).resolve()
        if str(workspace) != pin["workspace"] or pin["allowed_file"] != SOURCE_FILE:
            raise ValueError("Repair workspace identity or allowed source scope changed.")
        if git(workspace, "ls-files", "--others", "--exclude-standard"):
            raise ValueError("Repair contains unapproved untracked files.")
        changed = git(workspace, "diff", pin["base_commit"], "--name-only").splitlines()
        if changed != ([SOURCE_FILE] if require_changed else []):
            raise ValueError("Only the approved fulfillment source may change.")
        for name in git(workspace, "ls-files").splitlines():
            path = workspace / name
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(workspace):
                raise ValueError("Repair changed tracked file ownership.")
        return git(workspace, "rev-parse", "HEAD")


def write_pin(workspace, pin):
    path = Path(workspace).parent / "source-pin.json"
    path.write_text(json.dumps(pin, indent=2))
    path.chmod(0o600)
