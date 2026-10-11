"""Event buffer boundary, before model prompts or artifact serialization."""

from types import SimpleNamespace
import json
from tryfolk.browser_events import EventCapture


class Payload:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **options):
        return self.value


def test_private_values_are_removed_before_console_and_domain_events_are_retained():
    capture = EventCapture()
    key = "pk_snd_fixture"
    secret = "pay_fixture_secret_ABC123"
    url = "https://api.custom.hs.dodopayments.com/account/payment_methods?client_secret=" + secret
    capture.console(
        SimpleNamespace(
            params=Payload({"type": "warning", "args": [{"value": key + " " + secret + " " + url}]})
        )
    )
    capture.network_log(
        SimpleNamespace(
            message="Blocked request by domain policy",
            data=Payload(
                {
                    "url": url,
                    "nested": {
                        "frame": "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html?publishableKey="
                        + key
                        + "#parentOrigin=fixture"
                    },
                    "key": key,
                    "secret": secret,
                }
            ),
        )
    )
    retained = json.dumps(capture.console_events + capture.blocked_domains)
    assert key not in retained and secret not in retained
    assert "?client_secret=" not in retained and "?publishableKey=" not in retained
    assert "https://api.custom.hs.dodopayments.com/account/payment_methods" in retained
