"""Real local Git boundary; synthetic checkpoint ID is not Entire capture evidence."""

import hashlib
import subprocess
from tryfolk.repair import checkpoint_commit


def test_checkpoint_trailer_is_linked_before_revision_registration_without_source_change(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "repair")
    source = tmp_path / "fulfillment.py"
    source.write_text('def should_grant(payment):\n    return payment["status"] == "succeeded"\n')
    git("add", ".")
    git(
        "-c", "user.name=Test", "-c", "user.email=test@tryfolk.invalid", "commit", "-m", "Repair owned source"
    )
    before = git("rev-parse", "HEAD")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    after = checkpoint_commit(tmp_path, "01M4KENRAJQXV9A4G8B7H478JF")
    assert after == git("rev-parse", "HEAD") and after != before
    assert "Entire-Checkpoint: 01M4KENRAJQXV9A4G8B7H478JF" in git("log", "-1", "--format=%B")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest and not git("status", "--porcelain")
