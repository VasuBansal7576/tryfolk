"""Real isolated Git source ownership, without models, keys, or network."""

import subprocess
import pytest
from tryfolk.source_workspace import OwnedSource, source_fingerprint


def baseline(tmp_path):
    root = tmp_path / "owned"
    target = root / "dodo-target"
    (target / "lib").mkdir(parents=True)
    (target / "lib/fulfillment-policy.ts").write_text("export function disposition() { return false; }\n")
    (target / "LICENSE.md").write_text("Test-only license fixture")
    (target / "SOURCE.md").write_text("Test-only provenance")
    (target / ".gitignore").write_text(".env.local\nnode_modules/\n.next/\n")
    for args in (
        ("init", "-b", "main"),
        ("add", "."),
        ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    (target / ".env.local").write_text("PRIVATE_FIXTURE_MARKER=must-not-copy\n")
    return root, target


def test_snapshot_preserves_license_and_exact_baseline_excludes_ignored_secrets(tmp_path):
    root, target = baseline(tmp_path)
    workspace = tmp_path / "repair/source"
    source = OwnedSource(root)
    pin = source.snapshot(workspace, expected_digest=source_fingerprint(target))
    assert not (workspace / ".env.local").exists()
    assert (workspace / "LICENSE.md").read_text() == "Test-only license fixture"
    assert pin["source_digest"] == source_fingerprint(workspace)
    assert (
        subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain"], capture_output=True, text=True
        ).stdout
        == ""
    )
    assert source.verify(workspace, pin, require_changed=False) == pin["base_commit"]
    with pytest.raises(ValueError):
        source.snapshot(tmp_path / "another/source", expected_digest="0" * 64)
    (target / "lib/fulfillment-policy.ts").write_text("uncommitted mutation")
    with pytest.raises(ValueError):
        source.snapshot(tmp_path / "third/source", expected_digest=source_fingerprint(target))


def test_only_fulfillment_body_can_change_and_commit_or_test_bypasses_are_rejected(tmp_path):
    root, target = baseline(tmp_path)
    source = OwnedSource(root)
    workspace = tmp_path / "repair/source"
    pin = source.snapshot(workspace, expected_digest=source_fingerprint(target))
    repaired = workspace / "lib/fulfillment-policy.ts"
    repaired.write_text(repaired.read_text().replace("return false;", "return true;"))
    assert source.verify(workspace, pin, require_changed=True) == pin["base_commit"]
    (workspace / "SOURCE.md").write_text("changed provenance")
    with pytest.raises(ValueError):
        source.verify(workspace, pin, require_changed=True)
    (workspace / "SOURCE.md").write_text("Test-only provenance")
    (workspace / "extra.txt").write_text("unapproved file")
    with pytest.raises(ValueError):
        source.verify(workspace, pin, require_changed=True)


def test_tracked_source_symlink_is_not_a_grant_to_read_outside_owned_target(tmp_path):
    root, target = baseline(tmp_path)
    (target / "lib/link.ts").symlink_to(tmp_path / "outside-secret")
    subprocess.run(
        ["git", "-C", str(root), "add", "dodo-target/lib/link.ts"], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "link",
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError):
        OwnedSource(root).snapshot(tmp_path / "repair/source", expected_digest="0" * 64)


def test_entire_installer_resolves_private_hooks_without_changing_launcher_or_other_config(tmp_path, monkeypatch):
    from tryfolk.source_workspace import entire_installer_env

    root, target = baseline(tmp_path)
    workspace = tmp_path / "repair/source"
    OwnedSource(root).snapshot(workspace, expected_digest=source_fingerprint(target))
    shared = tmp_path / "protected-launcher-hooks"
    shared.mkdir()
    wrapper = shared / "prepare-commit-msg"
    wrapper.write_text("test-only protected launcher marker\n")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(shared))
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "Retained launcher identity")
    env = entire_installer_env(workspace)
    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert env["GIT_CONFIG_VALUE_0"] == str(shared)
    assert env["GIT_CONFIG_KEY_1"] == "user.name"
    assert env["GIT_CONFIG_VALUE_1"] == "Retained launcher identity"
    result = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--git-path", "hooks"],
        env=env, check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == str(workspace / ".git/hooks")
    assert subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name"],
        env=env, check=True, capture_output=True, text=True,
    ).stdout.strip() == "Retained launcher identity"
    # A separate ordinary Git subprocess must still see the original wrapper.
    assert subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--git-path", "hooks"],
        check=True, capture_output=True, text=True,
    ).stdout.strip() == str(shared)
    assert wrapper.read_text() == "test-only protected launcher marker\n"


def test_entire_installer_rejects_shared_git_directories_or_hook_redirection(tmp_path, monkeypatch):
    from tryfolk.source_workspace import entire_installer_env

    root, target = baseline(tmp_path)
    workspace = tmp_path / "repair/source"
    OwnedSource(root).snapshot(workspace, expected_digest=source_fingerprint(target))
    subprocess.run(
        ["git", "-C", str(workspace), "config", "core.hooksPath", str(tmp_path / "outside")],
        check=True, capture_output=True,
    )
    with pytest.raises(ValueError, match="private hooks"):
        entire_installer_env(workspace)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "core.hooksPath", str(workspace / ".git/hooks")],
        check=True, capture_output=True,
    )
    monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    with pytest.raises(ValueError, match="redirect"):
        entire_installer_env(workspace)
    monkeypatch.delenv("GIT_DIR")
    (workspace / ".git").rename(workspace / "private-git")
    (workspace / ".git").write_text("gitdir: private-git\n")
    with pytest.raises(ValueError, match="standalone"):
        entire_installer_env(workspace)
