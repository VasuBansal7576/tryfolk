"""External snapshot fixtures; the genuine SDK local probe is separate evidence."""

import pytest
from tryfolk.observed_controls import frame_control


def snapshot(frame_url="https://test.checkout.dodopayments.com/card"):
    return {
        "formatted_tree": "[1-1] RootWebArea\n  [0-2] RootWebArea\n    [0-3] textbox: Card number\n    [0-4] button: Pay now",
        "xpath_map": {
            "1-1": "/",
            "0-2": "/html[1]/body[1]/iframe[1]",
            "0-3": "/html[1]/body[1]/iframe[1]/html[1]/body[1]/input[1]",
            "0-4": "/html[1]/body[1]/iframe[1]/html[1]/body[1]/button[1]",
        },
        "url_map": {"1-1": "https://test.checkout.dodopayments.com/session", "0-2": frame_url},
    }


def test_cross_frame_metadata_uses_actual_sdk_snapshot_label_and_frame_url():
    state = frame_control("xpath=/html[1]/body[1]/iframe[1]/html[1]/body[1]/input[1]", snapshot())
    assert state["tag"] == "INPUT" and state["label"] == "Card number"
    assert state["frame_url"] == "https://test.checkout.dodopayments.com/card"
    with pytest.raises(ValueError):
        frame_control("xpath=/not/fresh/input[1]", snapshot())
    with pytest.raises(ValueError):
        frame_control(
            "xpath=/html[1]/body[1]/iframe[1]/html[1]/body[1]/input[1]",
            {**snapshot(), "formatted_tree": "[0-3] textbox"},
        )
