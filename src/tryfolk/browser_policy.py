"""Exact test origins and server-bound synthetic checkout values.

Card data comes from Dodo's official miscellaneous/testing-process documentation.
It is public test data. No model may supply a card, credential or arbitrary fill.
"""

import re
import math
from urllib.parse import urlsplit

DODO_CHECKOUT = "https://test.checkout.dodopayments.com"
DODO_RETURN = "https://test.dodopayments.com"
DODO_SECURE_FRAME = "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html"
TEST_FIELDS = {
    "card_number": "4242424242424242",
    "card_expiry": "06/32",
    "card_month": "06",
    "card_year": "2032",
    "card_cvc": "123",
    "card_name": "Tryfolk Test Customer",
    "address": "123 Test Street",
    "address_query": "123 Test Street",
    "city": "New York",
    "state": "New York",
    "postal": "10001",
    "country": "US",
}
AUTOCOMPLETE = {
    "cc-number": "card_number",
    "cc-exp": "card_expiry",
    "cc-exp-month": "card_month",
    "cc-exp-year": "card_year",
    "cc-csc": "card_cvc",
    "cc-name": "card_name",
    "name": "card_name",
    "street-address": "address",
    "address-line1": "address",
    "address-level2": "city",
    "address-level1": "state",
    "postal-code": "postal",
    "country": "country",
    "country-name": "country",
}
SELECT_METHODS = ("selectOption", "selectOptionFromDropdown")
FIELD_METHODS = ("fill", "type", *SELECT_METHODS)


def exact_origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Browser URL must have an approved exact origin without credentials.")
    # Accessing port also rejects malformed authorities; explicit extra ports stay distinct.
    _ = parsed.port
    return f"{parsed.scheme}://{parsed.netloc}"


class BrowserPolicy:
    def __init__(self, origin, provider):
        parsed = urlsplit(origin)
        ports = range(3300, 3400) if provider == "dodo" else range(3100, 3200)
        if (
            provider not in ("dodo", "local-fixture")
            or exact_origin(origin) != origin
            or parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port not in ports
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Browser session requires a registered owned loopback origin.")
        self.origin, self.provider = origin, provider
        self.origins = (origin, DODO_CHECKOUT) if provider == "dodo" else (origin,)

    def check_url(self, url):
        if exact_origin(url) not in self.origins:
            raise ValueError("Browser left the exact registered app or approved Dodo test checkout origin.")

    def bind(self, action, control, url):
        """Bind a freshly observed control to an approved operation; never trust its proposed value."""
        self.check_url(url)
        frame_url = control.get("frame_url")
        frame_generation = control.get("frame_generation")
        if frame_url or "/iframe[" in str(action.get("selector") or ""):
            if (
                self.provider != "dodo"
                or exact_origin(url) != DODO_CHECKOUT
                or frame_url != DODO_SECURE_FRAME
                or control.get("destination") != frame_url
                or action.get("method") not in FIELD_METHODS
                or not str(action.get("selector") or "").startswith("xpath=/")
                or action["selector"].count("/iframe[") != 1
                or not isinstance(frame_generation, (int, float))
                or isinstance(frame_generation, bool)
                or not math.isfinite(frame_generation)
                or frame_generation <= 0
                or control.get("frame_context_verified") is not True
                or not re.fullmatch(r"[a-f0-9]{64}", control.get("frame_config_id") or "")
            ):
                raise ValueError(
                    "Only fresh synthetic card fields in the exact direct secure frame are approved."
                )
        if (
            control.get("present") is False
            or control.get("visible") is False
            or control.get("disabled")
            or re.search(r"\bprocessing\b", str(control.get("label") or ""), re.I)
        ):
            raise ValueError("Observed control is missing, hidden, disabled or processing.")
        if not frame_url:
            self.check_url(control.get("destination") or url)
        action = dict(action)
        if not isinstance(action.get("selector"), str) or not action["selector"]:
            raise ValueError("Observed control has no selector.")
        method = action.get("method")
        metadata = {"url": url, "capability": False}
        if frame_url:
            metadata.update(
                frame_url=frame_url,
                frame_generation=frame_generation,
                frame_config_id=control["frame_config_id"],
            )
        if method == "click":
            if action.get("arguments"):
                raise ValueError("A click cannot accept model arguments.")
            destination = urlsplit(control.get("destination") or url)
            owned_return = (
                self.provider == "dodo"
                and exact_origin(url) == self.origin
                and urlsplit(url).path == "/checkout/return"
            )
            art_pack = destination.path == "/special-downloads" and (
                not owned_return
                or (
                    destination.geturl() == self.origin + "/special-downloads"
                    and control.get("tag") == "A"
                )
            )
            metadata["capability"] = bool(
                self.provider == "dodo"
                and exact_origin(destination.geturl()) == self.origin
                and destination.path == "/api/target/download"
            )
            metadata["kind"] = (
                "download"
                if metadata["capability"]
                else {"test-login": "login", "buy-art-pack": "purchase", "confirm-art-pack": "confirm"}.get(
                    control.get("id")
                )
                or ("art_pack" if art_pack else "click")
            )
            if (
                self.provider == "dodo"
                and exact_origin(url) == DODO_CHECKOUT
                and control.get("tag") == "BUTTON"
                and re.match(r"^pay(?:\s+now)?(?:\s|$)", str(control.get("label") or "").strip(), re.I)
            ):
                metadata["kind"] = "pay"
            if (
                self.provider == "dodo"
                and exact_origin(url) == DODO_CHECKOUT
                and control.get("tag") == "BUTTON"
                and re.fullmatch(r"enter (?:address )?manually", str(control.get("label") or "").strip(), re.I)
            ):
                if control.get("center_hit_self") is not True:
                    raise ValueError("Manual control must be freshly hittable in the current document.")
                metadata["diagnostic_manual"] = True
                metadata["manual_variant"] = (
                    "enter_manually" if str(control["label"]).strip().lower() == "enter manually"
                    else "enter_address_manually"
                )
            if (
                self.provider == "dodo"
                and exact_origin(url) == DODO_CHECKOUT
                and control.get("tag") == "BUTTON"
                and control.get("id") == "step1"
                and re.fullmatch(r"continue\s+to\s+payment", str(control.get("label") or "").strip(), re.I)
            ):
                metadata["kind"] = "billing_continue"
            action["arguments"] = []
        elif method in FIELD_METHODS:
            if self.provider != "dodo" or exact_origin(url) != DODO_CHECKOUT:
                raise ValueError("Synthetic fields are allowed only on the approved hosted test checkout.")
            label = " ".join(
                str(control.get(k) or "") for k in ("label", "placeholder", "name", "id")
            ).lower()
            if control.get("type") in ("password", "file", "hidden") or re.search(
                r"password|api.key|secret|token", label
            ):
                raise ValueError("Credential or hidden fields cannot be filled.")
            tokens = (control.get("autocomplete") or "").lower().split()
            field = next((AUTOCOMPLETE[t] for t in tokens if t in AUTOCOMPLETE), None)
            patterns = (
                (r"card.?number", "card_number"),
                (r"expir|expiry|mm\s*/\s*yy", "card_expiry"),
                (r"cvv|cvc|security.?code", "card_cvc"),
                (r"name.*card|cardholder|full.?name", "card_name"),
                (r"postal|zip.?code", "postal"),
                (r"country", "country"),
                (r"street|address.?line(?:.?1)?|billing.?address", "address"),
                (r"city", "city"),
                (r"state|province", "state"),
            )
            if not field:
                field = next((f for pattern, f in patterns if re.search(pattern, label)), None)
            if not field or control.get("tag") not in ("INPUT", "SELECT", "TEXTAREA"):
                raise ValueError("Checkout field is outside the documented synthetic input contract.")
            if field == "address" and (
                control.get("tag") == "INPUT" and control.get("type") == "text"
                and tokens == ["off"] and not control.get("name") and not control.get("id")
                and control.get("role") == "combobox" and control.get("aria_autocomplete") == "list"
            ):
                # The hosted address search holds query state, not addressLine.
                # Explicit autocomplete or form identity remains authoritative.
                # Only the retained unnamed query signature is separated.
                field = "address_query"
            if frame_url and not field.startswith("card_"):
                raise ValueError("The secure frame grants synthetic card fields only.")
            value = TEST_FIELDS[field]
            if method in SELECT_METHODS:
                if control.get("tag") != "SELECT" or value not in control.get("options", []):
                    raise ValueError("Approved synthetic option is absent from this fresh dropdown.")
            elif control.get("tag") == "SELECT":
                raise ValueError("Dropdowns require a single bounded selectOption action.")
            action["arguments"] = [value]
            metadata["field"] = field
            action["description"] = f"Enter synthetic test {field.replace('_', ' ')}"
        else:
            raise ValueError("Unsupported browser method; no arbitrary tool or navigation.")
        action["_tryfolk"] = metadata
        self.validate(action, url)
        return action

    def validate(self, action, url=None):
        metadata = action.get("_tryfolk") or {}
        observed_url = metadata.get("url")
        if not isinstance(observed_url, str) or (url is not None and url != observed_url):
            raise ValueError("Action is not bound to the current observed document.")
        self.check_url(observed_url)
        if metadata.get("frame_url") or "/iframe[" in str(action.get("selector") or ""):
            if (
                metadata.get("frame_url") != DODO_SECURE_FRAME
                or exact_origin(observed_url) != DODO_CHECKOUT
                or not str(metadata.get("field") or "").startswith("card_")
                or action.get("method") not in FIELD_METHODS
                or not str(action.get("selector") or "").startswith("xpath=/")
                or action["selector"].count("/iframe[") != 1
                or not re.fullmatch(r"[a-f0-9]{64}", metadata.get("frame_config_id") or "")
            ):
                raise ValueError("Action left the exact observed secure card frame contract.")
        if action.get("method") == "click" and not action.get("arguments"):
            return
        field = metadata.get("field")
        if (
            self.provider != "dodo"
            or exact_origin(observed_url) != DODO_CHECKOUT
            or action.get("method") not in FIELD_METHODS
            or field not in TEST_FIELDS
            or action.get("arguments") != [TEST_FIELDS[field]]
        ):
            raise ValueError("Browser action contains an unapproved value or method.")

    def validate_completion(self, action, control, url):
        self.validate(action, url)
        if action["_tryfolk"].get("frame_url") and (
            control.get("frame_url") != action["_tryfolk"]["frame_url"]
            or control.get("frame_generation") != action["_tryfolk"]["frame_generation"]
            or control.get("frame_config_id") != action["_tryfolk"]["frame_config_id"]
            or control.get("frame_context_verified") is not True
        ):
            raise ValueError("Secure frame changed before synthetic field completion was verified.")
        if action.get("method") not in FIELD_METHODS:
            return
        actual = str(control.get("value") or "").strip()
        expected = action["arguments"][0]
        if action["_tryfolk"]["field"] in ("card_number", "card_expiry"):
            actual, expected = re.sub(r"[\s/-]", "", actual), re.sub(r"[\s/-]", "", expected)
        if actual != expected:
            raise ValueError("Observed field did not retain the completed approved synthetic value.")
