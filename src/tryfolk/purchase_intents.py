"""Prepare actual model intentions and insert them into saved TinyPerson state."""

from copy import deepcopy
import json

from .bulk_personas import PurchaseIntent, validated_purchase_plan
from .scenarios import digest


def prepare_intents(saved_population, brief, build, tiny_person, generate):
    population = deepcopy(saved_population)
    if not population or len({person["id"] for person in population}) != len(population):
        raise ValueError("Distinct actual saved customers are required.")
    if (not brief.get("observed") or brief.get("provider") != "dodo"
            or brief.get("measured_payment") is not False):
        raise ValueError("Actual Dodo BrowserUse discovery is required; it cannot establish payment outcomes.")
    requests = [{"persona_id": person["id"], "profile": person["profile"],
                 "supported_initial_conditions": person["conditions"],
                 "existing_customer_goal_memory": person["initial_semantic_memory"]} for person in population]
    intent_schema = PurchaseIntent.model_json_schema()
    # One slot is one observed control, including every billing/card field. The
    # actual first checkout needed fourteen slots just to reach Pay. Reserve a
    # complete bounded route; impatience remains in unexpected_state decisions.
    intent_schema["properties"]["max_steps"]["minimum"] = 24
    schema = {"type": "object", "properties": {person["id"]: intent_schema
                                               for person in population},
              "required": [person["id"] for person in population], "additionalProperties": False}
    intents = generate([
        {"role": "system", "content": "Prepare one individual conditional purchase intention for EACH saved "
         "customer. Respect its actual profile, existing goal and supported conditions. The observed product is "
         "one-time USD 1.00. Purchase at most once. Do not invent selectors, billing data, payment outcomes, "
         "provider claims or conditions. Execution must use fresh guarded browser controls. Unexpected state "
         "must follow the customer's patience and preferences; ordinary planned actions require no new reasoning. "
         "One step is ONE observed control or separately filled field, not a whole screen. The actual guarded "
         "checkout takes about fourteen actions to reach Pay plus confirmation, artwork navigation and download. "
         "Choose a bounded 24–48 action capacity for a complete route, while retaining impatience in unexpected-state choices."},
        {"role": "user", "content": json.dumps({"actual_observed_product": brief, "requests": requests})},
    ], schema, label="tinytroupe-saved-purchase-intents")
    if not isinstance(intents, dict) or set(intents) != set(schema["required"]):
        raise ValueError("A genuine individual model intention is required for every saved customer.")
    # Load the same SDK implementation used by Runtime. Embedding initialization,
    # restoring memory, semantic insertion and serialization are actual SDK work.
    for person in population:
        original_profile = digest(person["profile"])
        person["bulk_scenario_snapshot"] = {
            "id": "observed-one-time-special-downloads-goal", "observed_product": brief["observed"],
            "customer_goal": "Buy the one-time Special Downloads art pack once and use its protected artwork download.",
            "supported_initial_conditions": deepcopy(person["conditions"]),
            "hypothetical_problem_is_unobserved": True,
        }
        person["purchase_plan"] = {
            **PurchaseIntent.model_validate(intents[person["id"]]).model_dump(),
            "persona_id": person["id"], "profile_sha256": original_profile,
            "scenario_sha256": digest(person["bulk_scenario_snapshot"]),
            "source": "genuine_codex_batch_intent",
        }
        tiny_person.all_agents.pop(person["name"], None)
        sdk_person, _ = build(person)
        entry = {"type": "information", "content": json.dumps({
            "conditional_purchase_intent": person["purchase_plan"],
            "customer_goal": person["bulk_scenario_snapshot"]["customer_goal"],
            "hypothetical_problem_is_unobserved": True, "payment_or_fulfillment_measured": False,
        })}
        sdk_person.semantic_memory.store({"simulation_timestamp": None, **entry})
        sdk_person.listen(entry["content"])
        person["initial_semantic_memory"].append(entry)
        person["initial_snapshot"] = sdk_person.to_json()
        person["memory"] = sdk_person.episodic_memory.retrieve_all()
        if digest(person["profile"]) != original_profile:
            raise ValueError("Intention preparation must preserve the actual saved customer profile.")
        validated_purchase_plan(person)
        tiny_person.all_agents.pop(sdk_person.name, None)
    # SDK protocol serializes its own set-valued memory fields in this form.
    return json.loads(json.dumps(population, default=str))
