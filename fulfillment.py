"""Disclosed local paid-but-no-access defect. Codex repairs this owned file only."""

def should_grant(payment):
    # Deliberate defect: successful fixture payments are never fulfilled.
    return False
