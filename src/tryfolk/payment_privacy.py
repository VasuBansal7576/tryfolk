"""Sanitize payment evidence and model context, without changing actor inputs."""

import re
from .secure_frame import redact_frame_config

TEST_CARD = re.compile(r"\b4242[\s-]*4242[\s-]*4242[\s-]*4242\b|\b06\s*[/\-]\s*32\b|\b123\b")


def redact_payment(value):
    value = redact_frame_config(value)
    if isinstance(value, str):
        return TEST_CARD.sub("[redacted_test_card]", value)
    if isinstance(value, dict):
        card_action = str(value.get("_tryfolk", {}).get("field", "")).startswith("card_") if isinstance(value.get("_tryfolk"), dict) else False
        return {
            key: "[redacted_payment_payload]"
            if key in ("payment_method_data", "browser_info", "customer_acceptance", "payment_link", "private_payment_link", "private_contact", "expected_email")
            else ["[redacted_test_card]"] if key == "arguments" and card_action
            else redact_payment(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_payment(item) for item in value]
    return value
