"""Public passive-config diagnostics with explicitly invented private inputs."""

import json
from tryfolk.native_card_observation import native_configuration_facts


def configuration(return_url=None):
    return {
        "observer_available": True, "generation": 42, "ua": "private-native-UA",
        "native": {"config_bound": True, "generation": 42, "parent_source_bound": True,
                   "return_url": return_url, "return_field_present": True,
                   "return_type": "null" if return_url is None else "string", "return_source": "INIT"},
    }


def facts(observed, **changes):
    arguments = {
        "context": {"payment_id": "pay_private", "checkout_session_id": "cks_private"},
        "identity": {"approved": True, "provider_environment": "test_mode", "payment_correlated": True,
                     "payment_id": "pay_private", "checkout_session_id": "cks_private"},
        "owned_origin": "http://127.0.0.1:3300",
        "observed_parent_url": "https://test.checkout.dodopayments.com/private-alias?session=private-session",
        "current_parent_url": "https://test.checkout.dodopayments.com/private-alias?session=private-session",
        "observed_frame_url": "private-current-frame-config", "current_frame_url": "private-current-frame-config",
    }
    arguments.update(changes)
    return native_configuration_facts(observed, **arguments)


def test_bound_current_init_with_null_return_is_captured_before_the_return_predicate():
    result = facts(configuration())
    assert result["current_configuration_captured"]
    assert result["init_observed"] and result["configuration_bound"] and result["generation_current"]
    assert result["parent_current"] and result["frame_current"] and result["independent_test_identity_current"]
    assert result["return_field_present"] and not result["return_value_present"]
    assert result["return_type"] == "null" and result["return_source"] == "INIT"
    assert result["return_route"] is None
    assert "private" not in json.dumps(result)


def test_changed_generation_and_parent_retain_diagnostics_without_a_route():
    observed = configuration("http://127.0.0.1:3300/checkout/return")
    observed["generation"] = 43
    result = facts(observed)
    assert result["init_observed"] and result["configuration_bound"]
    assert result["return_value_present"] and result["return_source"] == "INIT"
    assert not result["generation_current"] and not result["current_configuration_captured"]
    assert result["return_route"] is None
    observed["generation"] = 42
    wrong = facts(observed, current_parent_url="https://test.checkout.dodopayments.com/other?session=private-other")
    assert not wrong["parent_current"] and wrong["return_route"] is None
    assert "private" not in json.dumps([result, wrong])


def test_absent_return_and_unobserved_init_are_distinct_and_never_discarded():
    observed = configuration()
    observed["native"].update(return_field_present=False, return_type="absent")
    absent = facts(observed)
    assert absent["current_configuration_captured"] and absent["return_type"] == "absent"
    assert not absent["return_field_present"] and not absent["return_value_present"]
    missing = facts({"observer_available": True, "generation": 42, "native": None})
    assert missing["observer_available"] and not missing["init_observed"]
    assert not missing["current_configuration_captured"] and missing["return_source"] == "unobserved"
    assert missing["return_route"] is None


def test_present_return_has_a_sanitized_route_only_after_current_identity_and_source_binding():
    observed = configuration("http://127.0.0.1:3300/checkout/return?payment_id=pay_private")
    result = facts(observed)
    assert result["return_value_present"] and result["return_type"] == "string"
    assert result["return_route"]["origin_role"] == "owned_target"
    assert result["return_route"]["current_payment_query_bound"]
    assert "private" not in json.dumps(result)
    changed = facts(observed, identity={"approved": False})
    assert changed["return_value_present"] and changed["configuration_bound"]
    assert not changed["independent_test_identity_current"] and changed["return_route"] is None


def test_confirm_source_origin_parent_and_generation_facts_remain_separate_from_init():
    observed = configuration('https://test.checkout.dodopayments.com/status/pay_private?session=private-session')
    observed['native'].update(return_source='CONFIRM', init_origin_bound=True, confirm_observed=True,
                              confirm_origin_bound=True, confirm_parent_source_bound=True, confirm_generation=42)
    result = facts(observed)
    assert result['return_source'] == 'CONFIRM' and result['init_origin_bound']
    assert result['confirm_observed'] and result['confirm_origin_bound'] and result['confirm_parent_source_bound']
    assert result['confirm_generation_current']
    observed['native']['confirm_generation'] = 41
    assert not facts(observed)['confirm_generation_current']
    assert 'private-session' not in json.dumps(result)


def observed_contacts(*, text='', inputs=()):
    """Execute the public passive reader against invented native input fixtures."""
    import subprocess
    from tryfolk.native_card_observation import READ_CONTACT
    harness = """const fs=require('fs'), vm=require('vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const inputs=data.inputs.map(e=>({type:'text',value:'',name:'',disabled:false,readOnly:false,...e}));
const document={body:{innerText:data.text},querySelectorAll:selector=>selector==='input'?inputs:selector==='input[type=email]'?inputs.filter(e=>e.type==='email'):[]};
process.stdout.write(JSON.stringify(vm.runInNewContext(data.reader,{document})));"""
    return json.loads(subprocess.check_output(['node', '-e', harness], input=json.dumps(
        {'reader': READ_CONTACT, 'text': text, 'inputs': list(inputs)}).encode()))


def test_native_readonly_text_contact_values_are_observed_without_collecting_names():
    result = observed_contacts(inputs=[
        {'name':'fullName','value':'Tryfolk Synthetic Customer','disabled':True,'readOnly':True},
        {'name':'email','value':'synthetic_fixture@tryfolk.invalid','disabled':True,'readOnly':True},
    ])
    assert result['email'] == 'synthetic_fixture@tryfolk.invalid'
    assert result['provider_name_observed']
    assert result['name_input_present'] and result['name_input_readonly'] and result['name_input_source_exact']
    assert 'Tryfolk Synthetic Customer' not in json.dumps(result)


def test_contact_reader_rejects_editable_hidden_or_multiple_synthetic_values():
    for name in ('Other Customer', 'tryfolk synthetic customer', 'Tryfolk  Synthetic Customer'):
        result=observed_contacts(inputs=[{'name':'fullName','value':name,'readOnly':True}])
        assert result['name_input_present'] and result['name_input_readonly']
        assert not result['name_input_source_exact'] and not result['provider_name_observed']
        assert name not in json.dumps(result)
    editable=observed_contacts(inputs=[
        {'name':'fullName','value':'Tryfolk Synthetic Customer'},
        {'name':'email','value':'synthetic_fixture@tryfolk.invalid'},
        {'type':'hidden','name':'email','value':'synthetic_hidden@tryfolk.invalid','readOnly':True},
    ])
    assert not editable['provider_name_observed'] and not editable['name_input_readonly']
    assert editable['email'] is None
    ambiguous=observed_contacts(text='synthetic_one@tryfolk.invalid', inputs=[
        {'name':'email','value':'synthetic_two@tryfolk.invalid','readOnly':True}])
    assert ambiguous['email'] is None
    absent=observed_contacts()
    assert not absent['name_input_present'] and not absent['provider_name_observed']
    duplicate=observed_contacts(text='synthetic_one@tryfolk.invalid', inputs=[
        {'name':'email','value':'synthetic_one@tryfolk.invalid','disabled':True}])
    assert duplicate['email']=='synthetic_one@tryfolk.invalid'
