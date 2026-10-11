"""Owner configuration for the existing fixture, bound to observed local source.

This declaration supplies selection metadata only. Actual SDK/payment/source
validation and budget gates remain independent.
"""

import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .scenario_evidence import CONDITIONS, FIXTURE

UPSTREAM = "ef7cbd980d8f1e57b036dc2af71ff05521b6175a"
FIELDS = ("scenario_capabilities", "scenario_conditions", "scenario_fixture_ids")


def validate_registration(config):
    try:
        origin = urlsplit(config["expected_origin"])
        capabilities = config["scenario_capabilities"]
        if (
            set(config)
            != {*FIELDS, "expected_origin", "expected_source_sha256", "expected_upstream_commit", "mode"}
            or origin.scheme != "http"
            or origin.hostname != "127.0.0.1"
            or origin.port != 3301
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
            or not re.fullmatch(r"[a-f0-9]{64}", config["expected_source_sha256"])
            or config["expected_upstream_commit"] != UPSTREAM
            or config["mode"] != "broken"
            or config["scenario_conditions"] != CONDITIONS
            or config["scenario_fixture_ids"] != [FIXTURE]
            or not isinstance(capabilities, dict)
            or set(capabilities) != {"target", "provider"}
            or any(
                not isinstance(values, list)
                or not 1 <= len(values) <= 32
                or any(not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", v) for v in values)
                for values in capabilities.values()
            )
        ):
            raise ValueError("Invalid existing-fixture registration declaration.")
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Invalid existing-fixture registration declaration.") from error
    return config


def load_registration(path):
    if not path:
        return None
    source = Path(path)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > 16_384:
        raise ValueError("Registration requires a bounded regular owner configuration file.")
    return validate_registration(json.loads(source.read_text()))


def bind_registration(manifest, config):
    if config is None:
        return {}
    validate_registration(config)
    expected = {
        "app_origin": config["expected_origin"],
        "source_sha256": config["expected_source_sha256"],
        "upstream_commit": config["expected_upstream_commit"],
        "mode": config["mode"],
        "source_clean": True,
        "provider_environment": "test_mode",
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        return {}
    return {key: config[key] for key in FIELDS}
