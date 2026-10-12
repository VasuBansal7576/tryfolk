"""Disclosed local paid-but-no-access defect. Codex repairs this owned file only."""

def should_grant(payment):
    return payment.get("status") == "succeeded"
