"""Private owned-session to payment-parent authority, without network I/O.

The reviewed hosted bundle's jZ POSTs the session confirm, and its ordinary
router copies URLSearchParams, sets explicit customization and from=session.
The retained source alone is not evidence of a current browser transition.
"""
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, quote

from .browser_policy import DODO_CHECKOUT

BILLING_SOURCE_SHA256 = "594824753cd5c68673c5719ec09a77477991381b992f82f1b2542fc44fda0895"


def valid_test_link(url):
    try:
        parsed = urlsplit(url) if isinstance(url, str) else urlsplit("")
        return (
            isinstance(url, str) and 0 < len(url) <= 4096
            and not re.search(r"[\s\\\x00-\x1f\x7f]", url)
            and f"{parsed.scheme}://{parsed.netloc}" == DODO_CHECKOUT
            and bool(parsed.path) and not (parsed.username or parsed.password or parsed.fragment)
            and "#" not in url
        )
    except ValueError:
        return False


class CheckoutNavigation:
    def __init__(self, checkout_id, entry_url, customization):
        self.checkout_id, self.entry_url = checkout_id, entry_url
        self.customization = customization
        self.source_generation = self.continue_generation = None
        self.submission = self.transition = None

    def _entry_current(self, document):
        return (
            valid_test_link(self.entry_url) and document.get("url") == self.entry_url
            and isinstance(document.get("generation"), (int, float))
            and document.get("body_present") is True
            and document.get("ready_state") in ("interactive", "complete")
        )

    def source_checks(self, digest, document):
        return {
            "reviewed_source_hash_bound": digest == BILLING_SOURCE_SHA256,
            "source_original_entry_current": document.get("url") == self.entry_url and valid_test_link(self.entry_url),
            "source_generation_available": isinstance(document.get("generation"), (int, float)),
        }

    def observe_source(self, digest, document):
        if all(self.source_checks(digest, document).values()):
            self.source_generation = document["generation"]
            return True
        return False

    def expect_continue(self, document):
        if self._entry_current(document) and self.continue_generation is None:
            self.continue_generation = document["generation"]
            return True
        return False

    def bind_submission(self, url, body, document):
        try:
            data = json.loads(body) if isinstance(body, str) and len(body) <= 20_000 else None
            approved = (
                self._entry_current(document) and self.submission is None
                and document["generation"] == self.continue_generation
                and url == DODO_CHECKOUT + "/api/checkout/sessions/" + quote(self.checkout_id, safe="") + "/confirm"
                and isinstance(data, dict) and set(data) == {"mode", "data"}
                and data["mode"] == "test" and isinstance(data["data"], dict)
            )
            if approved:
                self.submission = {"url": url, "body": body, "generation": document["generation"]}
            return approved
        except (ValueError, TypeError):
            return False

    def observe_response(self, raw, document):
        try:
            body = json.loads(raw) if len(raw) <= 20_000 else None
            link = body.get("payment_link") if isinstance(body, dict) else None
            if not (
                self.submission and self.transition is None and self._entry_current(document)
                and document["generation"] == self.submission["generation"] == self.source_generation
                and valid_test_link(link)
                # Ordinary top-level route only. Overlay/inline stay ungranted.
                and re.fullmatch(r"/session/[A-Za-z0-9_-]{1,256}", urlsplit(self.entry_url).path)
                and re.fullmatch(r"/[A-Za-z0-9_-]{8}", urlsplit(link).path)
                and not urlsplit(link).query and "?" not in link
                and isinstance(self.customization, dict)
                and set(self.customization) == {"hide_order_details", "hide_on_demand_tag"}
                and all(type(value) is bool for value in self.customization.values())
            ):
                return False
            pairs = parse_qsl(urlsplit(self.entry_url).query, keep_blank_values=True)
            # URLSearchParams.set removes duplicates and retains first position.
            def set_param(name, value):
                nonlocal pairs
                first = next((i for i, pair in enumerate(pairs) if pair[0] == name), len(pairs))
                pairs = [pair for pair in pairs if pair[0] != name]
                pairs.insert(first, (name, value))
            for key, name in (("hide_order_details", "showOrderDetails"), ("hide_on_demand_tag", "showOnDemandTag")):
                if self.customization[key]:
                    set_param(name, "false")
            set_param("from", "session")
            query = urlencode(pairs, safe="*-._", encoding="utf-8").replace("~", "%7E")
            parent = link + ("?" + query if query else "")
            self.transition = {"payment_link": link, "parent_url": parent, "generation": document["generation"]}
            return True
        except (ValueError, TypeError, UnicodeError):
            return False

    def parent_checks(self, payment_link, document):
        valid = valid_test_link(payment_link)
        exact = valid and document.get("url") == payment_link
        transition = self.transition or {}
        transformed = (
            valid and payment_link == transition.get("payment_link")
            and document.get("url") == transition.get("parent_url")
        )
        return {
            "independent_payment_link_available": valid,
            "owned_checkout_parent_exact": exact or transformed,
            "owned_payment_navigation_generation_current": exact or transformed
            and document.get("generation") == transition.get("generation"),
        }
