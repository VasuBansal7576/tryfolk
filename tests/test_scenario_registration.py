"""Local declaration admission fixtures; never actual registry writes/activation."""

from copy import deepcopy
import pytest

from tryfolk.scenario_registration import bind_registration
from tryfolk.scenario_evidence import CONDITIONS, FIXTURE


def declaration():
    return {
        "expected_origin": "http://127.0.0.1:3301",
        "expected_source_sha256": "a" * 64,
        "expected_upstream_commit": "ef7cbd980d8f1e57b036dc2af71ff05521b6175a",
        "mode": "broken",
        "scenario_capabilities": {"target": ["isolated_test_auth"], "provider": ["test_card_checkout"]},
        "scenario_conditions": CONDITIONS,
        "scenario_fixture_ids": [FIXTURE],
    }


def test_registration_metadata_binds_only_the_exact_observed_original_broken_owned_source():
    config = declaration()
    manifest = {
        "app_origin": config["expected_origin"],
        "source_sha256": config["expected_source_sha256"],
        "upstream_commit": config["expected_upstream_commit"],
        "mode": "broken",
        "source_clean": True,
        "provider_environment": "test_mode",
    }
    fields = bind_registration(manifest, config)
    assert fields["scenario_conditions"] == CONDITIONS
    assert fields["scenario_fixture_ids"] == [FIXTURE]
    assert "execution_ready" not in fields  # A declaration cannot clear the live-proof gate.
    for key, value in [
        ("mode", "healthy"),
        ("source_sha256", "b" * 64),
        ("source_clean", False),
        ("app_origin", "http://127.0.0.1:3338"),
        ("provider_environment", "live_mode"),
    ]:
        changed = deepcopy(manifest)
        changed[key] = value
        assert bind_registration(changed, config) == {}


@pytest.mark.parametrize("change", ["condition", "fixture", "capability_shape"])
def test_invalid_declaration_does_not_create_supported_runtime_metadata(change):
    config = declaration()
    if change == "condition":
        config["scenario_conditions"] = {**CONDITIONS, "locale": "fr-FR"}
    if change == "fixture":
        config["scenario_fixture_ids"] = ["invented"]
    if change == "capability_shape":
        config["scenario_capabilities"] = {"provider": True}
    with pytest.raises(ValueError):
        bind_registration({}, config)
