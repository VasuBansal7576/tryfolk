"""Private owned-session to payment-parent authority, without network I/O.

The reviewed hosted bundle's jZ POSTs the session confirm, and its ordinary
router copies URLSearchParams, sets explicit customization and from=session.
The retained source alone is not evidence of a current browser transition.
"""
import json
import re
import math
from urllib.parse import parse_qsl, urlencode, urlsplit, quote

from .browser_policy import DODO_CHECKOUT

BILLING_SOURCE_SHA256 = "594824753cd5c68673c5719ec09a77477991381b992f82f1b2542fc44fda0895"
NORMALIZER_SOURCE_SHA256 = "174be265126b29a5d7344bb545876bf50ba35f8bf816bc9858db998ef1ced24f"
RETURN_SOURCE_SHA256 = "78448c8d8f29f9f76004d79dc7c00ef22839fe72fc198b0316ef849945c02bfa"
STATUS_SOURCE_SHA256 = "6314a328275f2e6633034d55a11c76bc6459974b9caeb6b2a194dd42d8148fd0"
WALLET_SOURCE_SHA256 = "9031ab0923232e24875b5e9866a10e37a88c39089bba18524876429f6c6b2ec9"

# Server-only current-document read. Never include its result in SDK/model or
# evidence payloads; retain only normalization_checks, not this private map.
READ_NORMALIZATION_STATE = r"""() => {
  const url=location.href, generation=performance.timeOrigin, parsed=new URL(url);
  const key=parsed.searchParams.get('session');
  let value=null;
  try { if(key && /^sess_[A-Za-z0-9]{10}$/.test(key)) value=sessionStorage.getItem(key); } catch {}
  return {url,generation,storage_key:key && key.length<=64?key:null,
          storage_value:typeof value==='string' && value.length<=20000?value:null};
}"""


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
        self.normalizer_generation = self._armed_normalization = None
        self.return_source_generation = None
        self.status_destination = self.status_source_generation = self.status_protocol = None
        self.submission = self.transition = None

    def _entry_current(self, document):
        return (
            valid_test_link(self.entry_url) and document.get("url") == self.entry_url
            and isinstance(document.get("generation"), (int, float))
            and document.get("body_present") is True
            and document.get("ready_state") in ("interactive", "complete")
        )

    def source_checks(self, digest, document):
        if digest in (STATUS_SOURCE_SHA256, WALLET_SOURCE_SHA256):
            return {
                "reviewed_source_hash_bound": digest == (WALLET_SOURCE_SHA256 if (self.status_destination or "").endswith("/wallet-return") else STATUS_SOURCE_SHA256),
                "source_authorized_status_current": bool(self.status_destination) and document.get("url") == self.status_destination,
                "source_new_status_generation": isinstance(document.get("generation"), (int, float))
                and not isinstance(document.get("generation"), bool) and math.isfinite(document["generation"])
                and document["generation"] > 0 and document.get("generation") != self.return_source_generation,
                "status_source_generation_unchanged": self.status_source_generation in (None, document.get("generation")),
            }
        return {
            "reviewed_source_hash_bound": digest in (BILLING_SOURCE_SHA256, NORMALIZER_SOURCE_SHA256, RETURN_SOURCE_SHA256),
            "source_original_entry_current": document.get("url") == self.entry_url and valid_test_link(self.entry_url),
            "source_generation_available": isinstance(document.get("generation"), (int, float)),
        }

    def observe_source(self, digest, document):
        if all(self.source_checks(digest, document).values()):
            if digest in (STATUS_SOURCE_SHA256, WALLET_SOURCE_SHA256):
                self.status_source_generation = document["generation"]
            elif digest == NORMALIZER_SOURCE_SHA256:
                self.normalizer_generation = document["generation"]
            elif digest == RETURN_SOURCE_SHA256:
                self.return_source_generation = document["generation"]
            else:
                self.source_generation = document["generation"]
            return True
        return False

    def status_source_current(self, protocol):
        # The script capture binds this exact current live CDP document, not a
        # JavaScript read of a document whose next navigation is paused.
        return (bool(self.status_destination) and self.status_source_generation is not None
                and bool(self.status_protocol) and protocol == self.status_protocol
                and protocol.get("url") == self.status_destination)

    def return_source_current(self, document):
        return (self.return_source_generation is not None
                and self.return_source_generation == self.source_generation == self.continue_generation
                == (self.submission or {}).get("generation") == (self.transition or {}).get("generation")
                == document.get("generation"))

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
            and (document.get("url") == transition.get("parent_url")
                 or all(self.normalization_checks(document).values()))
        )
        return {
            "independent_payment_link_available": valid,
            "owned_checkout_parent_exact": exact or transformed,
            "owned_payment_navigation_generation_current": exact or transformed
            and document.get("generation") == transition.get("generation"),
        }

    def normalization_checks(self, document):
        """Exact reviewed SPA replacement plus freshly observed owned map."""
        transition = self.transition or {}
        observed = document.get("_normalization")
        observed = observed if isinstance(observed, dict) else {}
        current, derived = document.get("url"), transition.get("parent_url")
        key = None
        if valid_test_link(current):
            match = re.fullmatch(r"session=(sess_[A-Za-z0-9]{10})", urlsplit(current).query)
            if match:
                key = match[1]
        expected_pairs = parse_qsl(urlsplit(derived).query, keep_blank_values=True) if valid_test_link(derived) else []
        raw = observed.get("storage_value") if isinstance(observed, dict) else None
        mapping = None

        def unique_object(pairs):
            result = {}
            for name, value in pairs:
                if name in result:
                    raise ValueError("Duplicate storage member")
                result[name] = value
            return result

        try:
            if isinstance(raw, str) and 0 < len(raw) <= 20_000:
                mapping = json.loads(raw, object_pairs_hook=unique_object)
        except (ValueError, TypeError):
            pass
        shaped = isinstance(mapping, dict) and all(type(value) is str for value in mapping.values())
        return {
            "owned_billing_transition_available": self.transition is not None,
            "current_reviewed_normalizer_generation": self.normalizer_generation is not None
            and self.normalizer_generation == self.source_generation == self.continue_generation
            == (self.submission or {}).get("generation") == transition.get("generation") == document.get("generation"),
            "exact_same_Test_payment_path": valid_test_link(current) and valid_test_link(derived)
            and urlsplit(current).path == urlsplit(derived).path,
            "sole_source_derived_session_query": key is not None,
            "fresh_storage_document_and_generation": isinstance(observed, dict)
            and set(observed) == {"url", "generation", "storage_key", "storage_value"}
            and observed.get("url") == current and observed.get("generation") == document.get("generation")
            and type(observed.get("generation")) in (int, float),
            "fresh_storage_key_matches_current_query": key is not None and observed.get("storage_key") == key,
            "current_primary_document_ready": document.get("body_present") is True
            and document.get("ready_state") in ("interactive", "complete"),
            "storage_JSON_bounded": isinstance(raw, str) and 0 < len(raw) <= 20_000,
            "storage_map_has_only_string_values": shaped,
            # JS's plain object overwrites duplicate query names. Its special
            # __proto__ setter is deliberately unsupported rather than ignored.
            "source_query_object_supported": bool(expected_pairs) and not any(name == "__proto__" for name, _ in expected_pairs),
            "source_query_requires_fresh_session": not next((value for name, value in expected_pairs if name == "session"), ""),
            "storage_map_equals_exact_source_query_object": shaped and bool(expected_pairs)
            and mapping == dict(expected_pairs),
        }

    def freeze_parent(self, payment_link, document):
        if self._armed_normalization is not None or not all(self.parent_checks(payment_link, document).values()):
            return False
        if document.get("url") in (payment_link, (self.transition or {}).get("parent_url")):
            return True
        if not all(self.normalization_checks(document).values()):
            return False
        self._armed_normalization = {
            "url": document["url"], "generation": document["generation"],
            **document["_normalization"],
        }
        return True

    def armed_normalization_current(self, document):
        if self._armed_normalization is None:
            return True
        return all(self.normalization_checks(document).values()) and self._armed_normalization == {
            "url": document.get("url"), "generation": document.get("generation"),
            **(document.get("_normalization") or {}),
        }

    def parent_equality_facts(self, payment_link, document):
        """Closed diagnostics only. Component matches never confer authority.

        Creation flags describe the owned response's inputs, not independently
        observed effective hosted customization. Missing values cannot compare
        equal merely because both sides are absent.
        """
        transition = self.transition or {}
        current, billing, derived = document.get("url"), transition.get("payment_link"), transition.get("parent_url")

        def equal_urls(left, right):
            return valid_test_link(left) and valid_test_link(right) and left == right

        def equal_component(left, right, component):
            if not (valid_test_link(left) and valid_test_link(right)):
                return False
            a, b = urlsplit(left), urlsplit(right)
            if component == "origin":
                return (a.scheme, a.netloc) == (b.scheme, b.netloc)
            return getattr(a, component) == getattr(b, component)

        def equal_generations(left, right):
            return type(left) in (int, float) and type(right) in (int, float) and left == right

        def equal_query_pairs(ordered):
            if not (valid_test_link(derived) and valid_test_link(current)):
                return False
            a, b = (parse_qsl(urlsplit(url).query, keep_blank_values=True) for url in (derived, current))
            return (a == b) if ordered else (sorted(a) == sorted(b))

        flags = self.customization
        flags_available = (
            isinstance(flags, dict) and set(flags) == {"hide_order_details", "hide_on_demand_tag"}
            and all(type(value) is bool for value in flags.values())
        )
        generation = transition.get("generation")
        return {
            "independent_payment_link_null": payment_link is None,
            "independent_payment_link_valid": valid_test_link(payment_link),
            "current_document_url_valid": valid_test_link(current),
            "independent_link_equals_current_document": equal_urls(payment_link, current),
            "owned_billing_transition_available": self.transition is not None,
            "billing_response_link_equals_independent_link": equal_urls(billing, payment_link),
            **{"billing_response_" + part + "_equals_independent": equal_component(billing, payment_link, part)
               for part in ("origin", "path", "query")},
            "derived_parent_equals_current_document": equal_urls(derived, current),
            **{"derived_parent_" + part + "_equals_current": equal_component(derived, current, part)
               for part in ("origin", "scheme", "netloc", "path", "query")},
            "derived_parent_query_pairs_equal_current": equal_query_pairs(False),
            "derived_parent_query_order_equal_current": equal_query_pairs(True),
            "billing_generation_equals_current": equal_generations(generation, document.get("generation")),
            "source_generation_equals_current": equal_generations(self.source_generation, document.get("generation")),
            "source_generation_equals_billing": equal_generations(self.source_generation, generation),
            "continue_generation_equals_billing": equal_generations(self.continue_generation, generation),
            "submission_generation_equals_billing": equal_generations((self.submission or {}).get("generation"), generation),
            "creation_customization_flags_available": flags_available,
            "creation_customization_hides_order_details": flags_available and flags["hide_order_details"],
            "creation_customization_hides_on_demand_tag": flags_available and flags["hide_on_demand_tag"],
        }

    def parent_query_facts(self, document):
        """Known parameter booleans and unrelated counts, never names/values.

        Query observations are diagnostics, not evidence of effective hosted
        customization and not a substitute for complete private URL equality.
        """
        known = {"from": "from", "order_details": "showOrderDetails", "on_demand_tag": "showOnDemandTag"}
        urls = {"entry": self.entry_url, "derived": (self.transition or {}).get("parent_url"),
                "current": document.get("url")}
        available = {role: valid_test_link(url) for role, url in urls.items()}
        pairs = {role: parse_qsl(urlsplit(url).query, keep_blank_values=True) if available[role] else []
                 for role, url in urls.items()}
        unrelated = {role: [pair for pair in items if pair[0] not in known.values()]
                     for role, items in pairs.items()}

        def values(role, name):
            return [value for key, value in pairs[role] if key == name]

        checks = {role + "_query_available": valid for role, valid in available.items()}
        for label, name in known.items():
            for role in urls:
                items = values(role, name)
                checks[role + "_" + label + "_present"] = bool(items)
                if label == "from":
                    checks[role + "_from_session_exact"] = items == ["session"]
                else:
                    checks[role + "_" + label + "_explicit_false"] = items == ["false"]
            expected, current = values("derived", name), values("current", name)
            checks["derived_current_" + label + "_values_equal"] = bool(expected) and bool(current) and expected == current
        for role in ("derived", "current"):
            bound = available["entry"] and available[role]
            checks[role + "_original_unrelated_pairs_retained"] = bound and sorted(unrelated["entry"]) == sorted(unrelated[role])
            checks[role + "_original_unrelated_pair_order_retained"] = bound and unrelated["entry"] == unrelated[role]
        return {
            "checks": checks,
            "counts": {key: count for role, items in unrelated.items()
                       for key, count in ((role + "_unrelated_pairs", len(items)),
                                          (role + "_unrelated_names", len({key for key, _ in items})))},
        }
