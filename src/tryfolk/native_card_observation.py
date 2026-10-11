"""Passive native configuration observation and owned-browser fingerprint reads.

This listener sends no bridge message, edits no input and performs no payment.
Private values stay in the owned page/runtime and never enter evidence or models.
"""

import re
from urllib.parse import parse_qsl, quote, urlsplit
from .browser_policy import DODO_CHECKOUT


def return_route_facts(raw, *, payment_id, checkout_id, owned_origin, checkout_url):
    """Describe a private native return without admitting it or retaining values.

    Static route/schema words are closed. Any other segment or field name is
    opaque; ID comparisons and nested owned returns remain booleans only.
    """
    segments = {"payment-status", "payments", "payment", "checkout", "session", "return", "redirect", "status", "success", "failed", "cancelled", "confirm", "confirmation", "api", "download"}
    fields = {"payment_id", "paymentId", "checkout_id", "checkoutId", "checkout_session_id", "session", "session_id", "status", "return_url", "returnUrl", "redirect_url", "redirectUrl", "redirect", "redirect_to", "url", "token"}
    try:
        parsed, current = urlsplit(raw), urlsplit(checkout_url)
    except (ValueError, TypeError):
        return {"origin_role": "invalid_url", "parsing_failed": True}
    origin = f"{parsed.scheme}://{parsed.netloc}"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    parent_query = dict(parse_qsl(current.query, keep_blank_values=True))
    encoded_placeholder = "%7BPAYMENT_ID%7D" in raw
    literal_placeholder = "{PAYMENT_ID}" in raw
    resolved = raw.replace("{PAYMENT_ID}", quote(payment_id, safe="")).replace("%7BPAYMENT_ID%7D", quote(payment_id, safe=""))
    destination = urlsplit(resolved)
    current_segments = set(current.path.split("/")) - {""}

    def skeleton(path):
        output = []
        for part in path.split("/"):
            output.append(
                part if not part or part in segments or part in ("{PAYMENT_ID}", "%7BPAYMENT_ID%7D")
                else ":payment_id" if part == quote(payment_id, safe="")
                else ":checkout_id" if part == quote(checkout_id, safe="")
                else ":current_checkout_route" if part in current_segments
                else ":opaque_segment"
            )
        return "/".join(output)

    owned_query_return = False
    for key, value in query:
        if key not in fields:
            continue
        try:
            nested = urlsplit(value)
            owned_query_return |= f"{nested.scheme}://{nested.netloc}" == owned_origin and nested.path == "/checkout/return" and not nested.username and not nested.password
        except ValueError:
            pass
    return {
        "origin_role": "owned_target" if origin == owned_origin else "test_checkout" if origin == DODO_CHECKOUT else "unapproved_origin",
        "template_path_skeleton": skeleton(parsed.path),
        "resolved_path_skeleton": skeleton(destination.path),
        "query_field_names": sorted({key if key in fields else ":unrecognized_field" for key, _ in query}),
        "literal_payment_placeholder": literal_placeholder,
        "encoded_payment_placeholder": encoded_placeholder,
        "current_payment_path_bound": quote(payment_id, safe="") in destination.path.split("/"),
        "current_payment_query_bound": any(value == payment_id for _, value in parse_qsl(destination.query, keep_blank_values=True)),
        "current_checkout_path_bound": quote(checkout_id, safe="") in destination.path.split("/"),
        "current_checkout_query_bound": any(value == checkout_id for _, value in query),
        "current_parent_query_bindings": {key: origin == DODO_CHECKOUT and value == parent_query[key] for key, value in query if key in fields and key in parent_query},
        "same_current_checkout_path": origin == DODO_CHECKOUT and parsed.path == current.path,
        "query_owned_return_bound": bool(owned_query_return),
        "duplicate_query_names": len(query) != len(dict(query)),
        "blank_query_values": any(not value for _, value in query),
        "userinfo_present": bool(parsed.username or parsed.password),
        "fragment_present": bool(parsed.fragment),
    }


def native_configuration_facts(observed, *, context, identity, owned_origin,
                               observed_parent_url, current_parent_url,
                               observed_frame_url, current_frame_url):
    """Retain config facts before testing a return; this grants no capability."""
    native = observed.get("native") if isinstance(observed.get("native"), dict) else {}
    source = native.get("return_source")
    source = source if source in ("INIT", "CONFIRM") else "unobserved"
    return_type = native.get("return_type")
    return_type = return_type if return_type in ("absent", "null", "string", "number", "boolean", "object", "undefined") else "unobserved"
    try:
        parent = urlsplit(current_parent_url)
        parent_current = observed_parent_url == current_parent_url and f"{parent.scheme}://{parent.netloc}" == DODO_CHECKOUT and not parent.username and not parent.password
    except (TypeError, ValueError):
        parent_current = False
    facts = {
        "observer_available": observed.get("observer_available") is True,
        "init_observed": native.get("config_bound") is True and source in ("INIT", "CONFIRM"),
        "configuration_bound": native.get("config_bound") is True,
        "init_origin_bound": native.get("init_origin_bound") is True,
        "confirm_observed": native.get("confirm_observed") is True,
        "confirm_origin_bound": native.get("confirm_origin_bound") is True,
        "confirm_parent_source_bound": native.get("confirm_parent_source_bound") is True,
        "confirm_generation_current": native.get("confirm_observed") is True and native.get("confirm_generation") == observed.get("generation"),
        "generation_current": bool(native) and native.get("generation") == observed.get("generation"),
        "parent_current": bool(parent_current and native.get("parent_source_bound") is True),
        "frame_current": bool(observed_frame_url) and observed_frame_url == current_frame_url,
        "independent_test_identity_current": identity.get("approved") is True and identity.get("provider_environment") == "test_mode" and identity.get("payment_correlated") is True and bool(context.get("payment_id")) and identity.get("payment_id") == context.get("payment_id") and bool(context.get("checkout_session_id")) and identity.get("checkout_session_id") == context.get("checkout_session_id"),
        "return_field_present": native.get("return_field_present") is True,
        "return_value_present": isinstance(native.get("return_url"), str) and bool(native["return_url"]),
        "return_type": return_type, "return_source": source,
        "features_present": native.get("features_present") is True,
        "pay_button_feature_present": native.get("pay_button_feature_present") is True,
        "pay_button_feature_type": native.get("pay_button_feature_type") if native.get("pay_button_feature_type") in ("absent", "null", "object", "boolean") else "unobserved",
    }
    facts["current_configuration_captured"] = all(facts[key] for key in (
        "observer_available", "init_observed", "configuration_bound", "generation_current",
        "parent_current", "frame_current", "independent_test_identity_current",
    ))
    facts["return_route"] = return_route_facts(
        native["return_url"], payment_id=context["payment_id"], checkout_id=context["checkout_session_id"],
        owned_origin=owned_origin, checkout_url=current_parent_url,
    ) if facts["current_configuration_captured"] and facts["return_value_present"] else None
    return facts


INIT_OBSERVER = """(() => {
 const approvedParent = %s;
 if(window===window.parent)return;
 let state=null;
 window.addEventListener('message',event=>{
  const message=event.data;
  if(event.source!==window.parent || event.origin!==approvedParent ||
     !message || message.__dodo_payments_msg__!==true || typeof message.nonce!=='string')return;
  const q=new URLSearchParams(location.search);
  if(message.type==='INIT' && message.publishableKey===q.get('publishableKey') &&
     message.clientSecret===q.get('clientSecret') && message.apiUrl===q.get('apiUrl')){
   if(state)return;
   const features=message.features;
   const button=features?.payButton;
   const present=!!button && typeof button==='object' && Object.hasOwn(button,'returnUrl');
   const value=present?button.returnUrl:null;
   state={config_bound:true,generation:performance.timeOrigin,parent_source_bound:true,init_origin_bound:true,
    return_url:value,return_field_present:present,return_source:'INIT',
    return_type:!present?'absent':value===null?'null':typeof value,
    features_present:!!features && typeof features==='object',
    pay_button_feature_present:!!features && Object.hasOwn(features,'payButton'),
    pay_button_feature_type:button===undefined?'absent':button===null?'null':typeof button};
  } else if(message.type==='CONFIRM' && state && typeof message.returnUrl==='string'){
   state.return_url=message.returnUrl;
   state.return_field_present=true;state.return_type='string';state.return_source='CONFIRM';
   state.confirm_observed=true;state.confirm_origin_bound=true;state.confirm_parent_source_bound=true;state.confirm_generation=performance.timeOrigin;
  }
 });
 Object.defineProperty(window,'__tryfolkNativeCardObservation',{value:()=>state?{...state}:null});
})()"""

READ_CONTEXT = """(() => ({
 observer_available:typeof window.__tryfolkNativeCardObservation==='function',
 native:window.__tryfolkNativeCardObservation?.()||null,
 generation:performance.timeOrigin,
 ua:navigator.userAgent,language:navigator.language,color_depth:screen.colorDepth,
 screen_height:screen.height,screen_width:screen.width,time_zone:new Date().getTimezoneOffset()
}))()"""

READ_CONTACT = """(() => {
 const text=document.body.innerText;
 const emails=[...new Set(text.match(/synthetic_[A-Za-z0-9_-]+@tryfolk\\.invalid/g)||[])];
 const names=[],emailInputs=[],types=new Set();
 let syntheticInput=false;
 for(const element of document.querySelectorAll('input')){
  if(!['text','email'].includes(element.type))continue;
  types.add(element.type);
  const locked=element.disabled||element.readOnly;
  if(element.name==='fullName')names.push(element);
  if(element.name==='email'||element.type==='email')emailInputs.push(element);
  if(locked&&/^synthetic_[A-Za-z0-9_-]+@tryfolk\\.invalid$/.test(element.value)){
   emails.push(element.value);syntheticInput=true;
  }
 }
 const unique=[...new Set(emails)];
 const returnEmail=emailInputs.length===1 && (emailInputs[0].disabled||emailInputs[0].readOnly)
  && /^synthetic_[A-Za-z0-9_-]+@tryfolk\\.invalid$/.test(emailInputs[0].value)
  && unique.length===1 && unique[0]===emailInputs[0].value;
 const exact=names.some(element=>(element.disabled||element.readOnly)&&element.value==='Tryfolk Synthetic Customer');
 return {email:unique.length===1?unique[0]:null,
  return_email_source_exact:returnEmail,url:location.href,generation:performance.timeOrigin,
  provider_name_observed:text.includes('Tryfolk Synthetic Customer')||exact,
  name_input_present:names.length>0,
  name_input_readonly:names.some(element=>element.disabled||element.readOnly),
  name_input_source_exact:exact,
  contact_text_input_present:types.has('text'),contact_email_input_present:types.has('email'),
  email_input_present:emailInputs.length>0,
  email_input_readonly:emailInputs.some(element=>element.disabled||element.readOnly),
  synthetic_email_input_observed:syntheticInput};
})()"""


def observer_script():
    import json

    return INIT_OBSERVER % json.dumps(DODO_CHECKOUT)


def browser_information(observed):
    ua = observed["ua"]
    device, os_type, version = "Unknown Device", "Unknown", ""
    for pattern, system, hardware in (
        (r"Macintosh;.*Mac OS X ([\d_]+)", "macOS", "Macintosh"),
        (r"iPhone;.*iPhone OS ([\d_]+)", "iOS", "iPhone"),
        (r"iPad;.*CPU OS ([\d_]+)", "iOS", "iPad"),
        (r"Windows NT ([\d.]+)", "Windows", "Unknown Device"),
    ):
        if match := re.search(pattern, ua):
            device, os_type, version = hardware, system, match.group(1).replace("_", ".")
            break
    else:
        if match := re.search(r"Android ([\d.]+)(?:;.*?([^;)]+)\s*\))?", ua):
            device, os_type, version = (match.group(2) or "").strip() or "Unknown Device", "Android", match.group(1)
        elif "Linux" in ua:
            os_type = "Linux"
    return {
        "user_agent": ua,
        "accept_header": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "language": observed["language"],
        "color_depth": observed["color_depth"] if observed["color_depth"] in (1, 4, 8, 15, 16, 24, 32, 48) else 24,
        "screen_height": observed["screen_height"], "screen_width": observed["screen_width"],
        "time_zone": observed["time_zone"], "java_enabled": True, "java_script_enabled": True,
        "device_model": device, "os_type": os_type, "os_version": version,
    }
