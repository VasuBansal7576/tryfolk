"""Pinned source customization contracts; no SDK/browser/provider operation."""
import importlib.util
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_cache", ROOT / "integrations/stagehand/native_cache.py")
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


def original_bundle():
    candidates = list((ROOT / "integrations/stagehand/.venv/lib").glob(
        "python*/site-packages/playwright/driver/package/lib/coreBundle.tryfolk-original.js"))
    if not candidates:
        pytest.skip("Actual reviewed local SDK source not installed; live Chrome probe remains required.")
    raw = candidates[0].read_bytes()
    assert cache.sha(raw) == cache.ORIGINAL_SHA256
    return raw


def test_pinned_driver_changes_only_cache_flag_and_restores_exact_original(tmp_path, monkeypatch):
    monkeypatch.delenv("TRYFOLK_NATIVE_HTTP_CACHE", raising=False)
    raw = original_bundle()
    path = tmp_path / "coreBundle.js"
    path.write_bytes(raw)
    first = cache.configure(path=path)
    assert path.read_bytes() == raw.replace(cache.NEEDLE, cache.REPLACEMENT)
    assert first["actual_driver_sha256"] == cache.PATCHED_SHA256
    assert cache.configure(path=path) == first  # No double patch.
    assert cache.configure(path=path, restore=True)["actual_driver_sha256"] == cache.ORIGINAL_SHA256
    assert path.read_bytes() == raw


def test_unknown_sdk_source_is_rejected_without_any_source_write(tmp_path):
    path = tmp_path / "coreBundle.js"
    raw = b"unreviewed source"
    path.write_bytes(raw)
    with pytest.raises(RuntimeError, match="differs from the reviewed pinned source"):
        cache.configure(path=path)
    assert path.read_bytes() == raw
    assert not path.with_name("coreBundle.tryfolk-original.js").exists()


def test_modified_original_backup_is_rejected(tmp_path):
    path = tmp_path / "coreBundle.js"
    raw = original_bundle()
    path.write_bytes(raw)
    path.with_name("coreBundle.tryfolk-original.js").write_bytes(b"not original")
    with pytest.raises(RuntimeError, match="backup differs"):
        cache.configure(path=path)
    assert path.read_bytes() == raw
