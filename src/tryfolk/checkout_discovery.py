"""Read-only diagnostic candidate discovery with closed, value-free evidence.

This preserves the capture driver's priorities and approved controls. It cannot
execute actions, verify native readiness, retry reads or extend a deadline.
"""

from dataclasses import dataclass
import re


PRIORITIES = {
    "country": 2, "address": 4, "city": 5, "state": 6, "postal": 7,
    "card_name": 9, "card_number": 10, "card_expiry": 11,
    "card_month": 11, "card_year": 12, "card_cvc": 13,
}
BILLING_FIELDS = frozenset({"country", "address", "city", "state", "postal"})
PHASES = frozenset({"owned_login", "owned_purchase", "primary_billing", "native_card"})
ELIGIBLE_KINDS = (*PRIORITIES, "login", "purchase", "manual", "billing_continue", "pay")
POLICY_REJECTIONS = {
    "Only fresh synthetic card fields in the exact direct secure frame are approved.": "policy_secure_frame",
    "Observed control is missing, hidden, disabled or processing.": "policy_control_unavailable",
    "Observed control has no selector.": "policy_selector_missing",
    "A click cannot accept model arguments.": "policy_click_arguments",
    "Synthetic fields are allowed only on the approved hosted test checkout.": "policy_field_origin",
    "Credential or hidden fields cannot be filled.": "policy_credential_or_hidden",
    "Checkout field is outside the documented synthetic input contract.": "policy_unsupported_field",
    "The secure frame grants synthetic card fields only.": "policy_secure_field",
    "Approved synthetic option is absent from this fresh dropdown.": "policy_synthetic_option_absent",
    "Dropdowns require a single bounded selectOption action.": "policy_dropdown_method",
    "Unsupported browser method; no arbitrary tool or navigation.": "policy_unsupported_method",
    "Browser URL must have an approved exact origin without credentials.": "policy_invalid_origin",
    "Browser left the exact registered app or approved Dodo test checkout origin.": "policy_origin_unapproved",
    "Action is not bound to the current observed document.": "policy_document_binding",
    "Action left the exact observed secure card frame contract.": "policy_action_frame",
    "Browser action contains an unapproved value or method.": "policy_action_value",
}
REJECTIONS = (
    "unsupported_snapshot_path", "missing_control", "hidden_control", "disabled_control",
    "policy_bind_rejected", "outside_field_contract", "completed_field",
    "prior_completion_currently_unverified",
    "already_clicked", "billing_completion_missing", "pay_prerequisites_missing",
    "manual_fields_already_visible",
    "secure_child_pay_control", "other_control",
    *POLICY_REJECTIONS.values(),
)


def completion_flags(fields):
    return {field: field in fields for field in PRIORITIES}


@dataclass
class CandidateDiscovery:
    candidates: list
    filled: set
    completed_cards: dict
    phase: str
    initial_filled: set
    observed_fields: set
    observed_completion: set
    current_completion_conflicts: set
    observed_manual_variants: set
    eligible_counts: dict
    rejection_counts: dict

    def evidence(self, *, document_stable):
        """Explicit schema; private actions/selectors/control values never serialize."""
        stable = document_stable is True
        return {
            "phase": self.phase,
            "document_stable": stable,
            "candidate_count": len(self.candidates),
            "eligible_counts": dict(self.eligible_counts),
            "rejection_counts": dict(self.rejection_counts),
            "completion_before": completion_flags(self.initial_filled),
            "observed_fields": completion_flags(self.observed_fields),
            "observed_completion": completion_flags(self.observed_completion),
            "current_completion_conflicts": completion_flags(self.current_completion_conflicts),
            "manual_controls_observed": {variant: variant in self.observed_manual_variants for variant in
                                         ("enter_manually", "enter_address_manually")},
            "completion_after_pass": completion_flags(self.filled),
            "committed_completion": completion_flags(self.filled if stable else self.initial_filled),
        }


async def discover_checkout_candidates(
    snapshot, read_control, policy, url, *, filled, completed_cards, clicked,
    phase, pay_actions=0, executed_fields=frozenset(),
):
    if phase not in PHASES:
        raise ValueError("Unknown diagnostic checkout phase")
    result = CandidateDiscovery(
        candidates=[], filled=set(filled), completed_cards=dict(completed_cards),
        phase=phase, initial_filled=set(filled), observed_fields=set(),
        observed_completion=set(), current_completion_conflicts=set(),
        observed_manual_variants=set(),
        eligible_counts={kind: 0 for kind in ELIGIBLE_KINDS},
        rejection_counts={reason: 0 for reason in REJECTIONS},
    )

    def reject(reason):
        result.rejection_counts[reason] += 1

    for path in dict.fromkeys(snapshot.get("xpath_map", {}).values()):
        if not re.search(r"/(?:button|input|select|a|textarea)\[\d+\]$", path):
            reject("unsupported_snapshot_path")
            continue
        selector = "xpath=" + path
        control = await read_control(selector)
        if not control.get("present"):
            reject("missing_control")
            continue
        if not control.get("visible"):
            reject("hidden_control")
            continue
        if control.get("disabled"):
            reject("disabled_control")
            continue
        method = (
            "selectOptionFromDropdown" if control.get("tag") == "SELECT"
            else "fill" if control.get("tag") in ("INPUT", "TEXTAREA") else "click"
        )
        try:
            action = policy.bind(
                {"selector": selector, "method": method, "arguments": [],
                 "description": "Fresh native checkout diagnostic control"}, control, url,
            )
        except ValueError as error:
            reject(POLICY_REJECTIONS.get(str(error), "policy_bind_rejected"))
            continue
        metadata = action["_tryfolk"]
        field, kind = metadata.get("field"), metadata.get("kind")
        if field:
            if field not in PRIORITIES:
                reject("outside_field_contract")
                continue
            result.observed_fields.add(field)
            current_completion = False
            try:
                policy.validate_completion(action, control, url)
                current_completion = True
                result.filled.add(field)
                result.observed_completion.add(field)
                if field.startswith("card_"):
                    result.completed_cards[field] = (
                        metadata.get("frame_config_id"), metadata.get("frame_generation"),
                    )
            except ValueError:
                pass
            if field in result.filled and not current_completion:
                result.current_completion_conflicts.add(field)
                reject("prior_completion_currently_unverified")
                if field in executed_fields:
                    raise ExecutedFieldChanged(field, result)
                result.filled.discard(field)
                result.completed_cards.pop(field, None)
            if field in result.filled:
                reject("completed_field")
                continue
            priority, eligible = PRIORITIES[field], field
        elif kind in ("login", "purchase"):
            if kind in clicked:
                reject("already_clicked")
                continue
            priority, eligible = {"login": 0, "purchase": 1}[kind], kind
        elif kind == "billing_continue":
            if kind in clicked:
                reject("already_clicked")
                continue
            if not BILLING_FIELDS <= result.filled:
                reject("billing_completion_missing")
                continue
            priority, eligible = 8, kind
        elif kind == "click" and control.get("tag") == "BUTTON" and re.fullmatch(
            r"enter (?:address )?manually", str(control.get("label") or "").strip(), re.I,
        ):
            variant = (
                "enter_manually" if str(control["label"]).strip().lower() == "enter manually"
                else "enter_address_manually"
            )
            result.observed_manual_variants.add(variant)
            if "manual" in clicked:
                reject("already_clicked")
                continue
            priority, eligible = 3, "manual"
            action["_tryfolk"]["diagnostic_manual"] = True
            action["_tryfolk"]["manual_variant"] = variant
        elif kind == "pay" and not pay_actions:
            if "/iframe[" in selector:
                reject("secure_child_pay_control")
                continue
            if not (BILLING_FIELDS | {"card_number", "card_cvc"}) <= result.filled or not (
                "card_expiry" in result.filled or {"card_month", "card_year"} <= result.filled
            ):
                reject("pay_prerequisites_missing")
                continue
            priority, eligible = 20, kind
        else:
            reject("other_control")
            continue
        result.candidates.append((priority, action))
        result.eligible_counts[eligible] += 1
    # Current visible fields prove an open form independently of click history.
    # Apply after the full pass so AX/control order cannot affect this decision.
    if {"city", "state", "postal"} <= result.observed_fields:
        manual = [a for _, a in result.candidates if a["_tryfolk"].get("diagnostic_manual")]
        result.candidates = [(p, a) for p, a in result.candidates if a not in manual]
        result.eligible_counts["manual"] -= len(manual)
        result.rejection_counts["manual_fields_already_visible"] += len(manual)
    return result


class ExecutedFieldChanged(ValueError):
    """A changed previously executed control stops rather than repeating a fill."""

    def __init__(self, field, discovery):
        self.field = field
        self.discovery = discovery
        super().__init__("Previously executed checkout field no longer verifies")
