"""Transparent pinned Playwright driver customization for owned Chrome profiles.

Only routing's cache-disable flag changes. Actual Fetch/request guards remain.
The environment opt-in applies to this Stagehand process's driver only. Native
Chrome caches actual provider responses under ordinary HTTP caching rules; this
module never constructs responses, fetches assets, or retries any request.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile


ORIGINAL_SHA256 = "549070af3acabb3efcc4f55bfe6210f9f7c2fcf633cf7eaa59bfe60719969171"
PATCHED_SHA256 = "6e00c5a144459d158b3a013bab83974ef03e29b212df6dd56591d4f21192bbc2"
NEEDLE = b'const cachePromise = info.session.send("Network.setCacheDisabled", { cacheDisabled: enabled });'
REPLACEMENT = b'const cachePromise = info.session.send("Network.setCacheDisabled", { cacheDisabled: enabled && process.env.TRYFOLK_NATIVE_HTTP_CACHE !== "true" });'


def driver_path():
    import playwright

    return Path(playwright.__file__).parent / "driver/package/lib/coreBundle.js"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path, raw):
    descriptor, temporary = tempfile.mkstemp(prefix=".tryfolk-cache-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(*, restore=False, path=None):
    path = path or driver_path()
    backup = path.with_name("coreBundle.tryfolk-original.js")
    with path.with_name(".tryfolk-native-cache.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        raw = path.read_bytes()
        observed = sha(raw)
        if observed not in (ORIGINAL_SHA256, PATCHED_SHA256):
            raise RuntimeError("Installed Playwright driver differs from the reviewed pinned source; no change applied.")
        if backup.exists() and sha(backup.read_bytes()) != ORIGINAL_SHA256:
            raise RuntimeError("Original Playwright backup differs from the reviewed source.")
        if restore:
            if observed == PATCHED_SHA256:
                if not backup.exists():
                    raise RuntimeError("Verified original driver backup required for restoration.")
                atomic_write(path, backup.read_bytes())
        elif observed == ORIGINAL_SHA256:
            if raw.count(NEEDLE) != 1:
                raise RuntimeError("Expected one reviewed cache flag; no change applied.")
            replacement = raw.replace(NEEDLE, REPLACEMENT)
            if sha(replacement) != PATCHED_SHA256:
                raise RuntimeError("Patched driver hash differs from the reviewed source.")
            if not backup.exists():
                atomic_write(backup, raw)
            atomic_write(path, replacement)
        actual = sha(path.read_bytes())
    expected = ORIGINAL_SHA256 if restore else PATCHED_SHA256
    if actual != expected:
        raise RuntimeError("Actual installed driver source failed verification.")
    if not restore:
        os.environ["TRYFOLK_NATIVE_HTTP_CACHE"] = "true"
    return {"original_driver_sha256": ORIGINAL_SHA256, "patched_driver_sha256": PATCHED_SHA256,
            "actual_driver_sha256": actual, "native_http_cache": not restore,
            "customization": "routing_cache_flag_only", "synthetic_responses": False,
            "financial_retries": False, "request_response_guards_preserved": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true")
    print(json.dumps(configure(restore=parser.parse_args().restore)))
