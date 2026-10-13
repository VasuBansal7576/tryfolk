"""Invented SDK/protocol/session fixture; no provider or access proof claimed."""

import pytest
from test_owned_download_navigation import owned_fixture
from tryfolk.browser_network import OwnedNavigationError


def settlement(destination):
    return dict(url=destination, transition_state="ready", ready_state="complete", body_present=True, busy=False)


async def spa(monkeypatch):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    destination = guard.policy.origin + "/special-downloads"
    await guard.prepare_owned_navigation(destination)
    # A real Next Link changes only history: no primary GET request is emitted,
    # so the existing route admission remains absent. Target/loader stay current.
    page.main_frame.url = destination
    return guard, page, state, identity, session, calls, destination


async def test_prepared_acknowledged_spa_retains_identity_then_allows_one_download(monkeypatch):
    guard, page, state, identity, session, calls, destination = await spa(monkeypatch)
    download = guard.policy.origin + "/api/target/download?artwork=0"
    assert not guard._owned_download_page_admitted
    saved = guard._owned_navigation
    with pytest.raises(OwnedNavigationError, match="committed_admitted_owned_app_document"):
        await guard.prepare_owned_navigation(download)
    guard._owned_navigation = saved
    checks = await guard.complete_owned_navigation(destination, sdk_success=True, settlement=settlement(destination))
    assert all(checks.values())
    assert guard._owned_download_page_admitted
    assert guard._owned_navigation is None
    await guard.prepare_owned_navigation(download)
    assert guard._owned_navigation["destination"] == download


@pytest.mark.parametrize("mutation", [
    "no_preparation", "different_destination", "not_acknowledged", "not_settled", "wrong_url",
    "new_loader", "new_target", "expired", "different_payment", "different_account", "unpaid",
    "session_401", "session_redirect", "session_payment", "session_account", "session_order",
    "session_ambiguous", "identity_changes_document", "session_changes_document",
])
async def test_spa_completion_never_admits_unbound_documents_or_identity(monkeypatch, mutation):
    guard, page, state, identity, session, calls, destination = await spa(monkeypatch)
    sdk_success = True
    settled = settlement(destination)
    if mutation == "no_preparation":
        guard._owned_navigation = None
    elif mutation == "different_destination":
        guard._owned_navigation["destination"] += "?foreign=1"
    elif mutation == "not_acknowledged":
        sdk_success = False
    elif mutation == "not_settled":
        settled["busy"] = True
    elif mutation == "wrong_url":
        page.main_frame.url = guard.policy.origin + "/elsewhere"
    elif mutation == "new_loader":
        state["parent_generation"] = 9000
    elif mutation == "new_target":
        original = guard.protocol_reader

        async def changed():
            tree = await original()
            tree["frame"]["id"] = "foreign_target"
            return tree

        guard.protocol_reader = changed
    elif mutation == "expired":
        guard.card_attempt.clock = lambda: 10**20
    elif mutation == "different_payment":
        identity["payment_id"] = "foreign_payment"
    elif mutation == "different_account":
        identity["ownership"]["account_id"] = "foreign_account"
    elif mutation == "unpaid":
        identity["payment_status"] = "processing"
    elif mutation == "session_401":
        session["http_status"] = 401
    elif mutation == "session_redirect":
        session["same_origin_no_redirect"] = False
    elif mutation == "session_payment":
        session["body"]["orders"][0]["payment_id"] = "foreign_payment"
    elif mutation == "session_account":
        session["body"]["ownership"]["account_id"] = "foreign_account"
    elif mutation == "session_order":
        session["body"]["orders"][0]["order_id"] = "foreign_order"
    elif mutation == "session_ambiguous":
        session["body"]["orders"] *= 2
    elif mutation == "identity_changes_document":
        original = guard.frame_verify

        async def changed(context):
            result = await original(context)
            state["parent_generation"] = 9000
            return result

        guard.frame_verify = changed
    elif mutation == "session_changes_document":
        original = guard.owned_session_reader

        async def changed():
            result = await original()
            state["parent_generation"] = 9000
            return result

        guard.owned_session_reader = changed
    with pytest.raises(OwnedNavigationError):
        await guard.complete_owned_navigation(destination, sdk_success=sdk_success, settlement=settled)
    assert not guard._owned_download_page_admitted
    assert guard._owned_navigation is None
