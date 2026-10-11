import pytest
from tryfolk.decisions import validated_action


def test_only_one_fresh_observed_single_step_action_can_execute():
    observation = [
        {"id": "fresh:0", "action": {"method": "click", "selector": "#buy", "arguments": []}},
        {"id": "fresh:1", "action": {"method": "fill", "selector": "#email", "arguments": ["arbitrary"]}},
    ]
    assert validated_action({"type": "BROWSER", "target": "fresh:0"}, observation) == {
        "method": "click",
        "selector": "#buy",
        "arguments": [],
    }
    for intent in [
        {"type": "BROWSER", "target": "old:0"},
        {"type": "BROWSER", "target": "fresh:1"},
        {"type": "NAVIGATE", "target": "http://evil.example"},
    ]:
        with pytest.raises(ValueError):
            validated_action(intent, observation)
