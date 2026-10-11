"""Invented manifest inputs at a public API boundary; no live evidence claims."""
import shutil
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from tryfolk.api import create_app
from tryfolk.config import Settings, Target
from tryfolk.target_provenance import dodo_provenance


def manifest(mode="broken"):
    return {
        "target_id":"tryfolk-owned-dodo-special-downloads",
        "upstream_commit":"ef7cbd980d8f1e57b036dc2af71ff05521b6175a",
        "source_commit":"a"*40,"source_sha256":"b"*64,"source_clean":True,
        "target_revision":"sha256:"+"c"*64,"provider_environment":"test_mode",
        "mode":mode,"fault":"payment-settled-fulfillment-suppressed" if mode=="broken" else None,
        "repair":{"source_file":"lib/fulfillment-policy.ts","disposition":"suppressed" if mode=="broken" else "granted"},
    }


def license_in(tmp_path):
    root=tmp_path/"source"
    root.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1]/"dodo-target/LICENSE.md",root/"LICENSE.md")
    return root


def test_owned_license_and_controlled_fault_derive_from_pinned_manifest_and_retained_license(tmp_path):
    root=license_in(tmp_path)
    value=dodo_provenance(manifest(),root)
    assert value["ownership"]=="owned_sandbox" and value["license"]=="MIT"
    assert value["controlled_fulfillment_defect"] is True
    assert value["baseline_target_revision"]==manifest()["target_revision"]
    assert dodo_provenance(manifest("healthy"),root)["controlled_fulfillment_defect"] is False
    for changes in ({"target_id":"a-merchant-not-this-target"},{"source_clean":False},{"mode":"healthy"},{"upstream_commit":"d"*40}):
        with pytest.raises(ValueError):
            dodo_provenance(manifest()|changes,root)
    (root/"LICENSE.md").write_text("Unlicensed fixture")
    with pytest.raises(ValueError):
        dodo_provenance(manifest(),root)


def test_approved_quote_and_saved_run_freeze_original_fault_provenance_without_repricing(tmp_path):
    value=dodo_provenance(manifest(),license_in(tmp_path))
    app=create_app(Settings(db_path=tmp_path/"db.sqlite",gates=(),targets=(Target(url="http://127.0.0.1:3301",provider="dodo",revision=manifest()["target_revision"],target_provenance=value),)))
    client=TestClient(app)
    quote=client.post('/quotes',json={"app_url":"http://127.0.0.1:3301","persona_count":1}).json()
    assert quote['target_provenance']==value
    run=client.post('/runs',json={"quote_id":quote['id']},headers={'Idempotency-Key':'unit-provenance'}).json()
    assert run['status']['target_provenance']==value
    assert run['status']['budget_contract']==quote
    # Owner config edits cannot retroactively change a persisted accepted quote or run.
    value['controlled_fulfillment_defect']=False
    fetched=client.get('/runs/'+run['id'],headers={'Authorization':'Bearer '+run['access_token']}).json()
    assert fetched['target_provenance']['controlled_fulfillment_defect'] is True
    assert fetched['budget']['reserved_microusd']==quote['reserved_microusd']
