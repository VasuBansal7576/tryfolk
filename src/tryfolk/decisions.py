"""One observed action is the only authority to perform a browser mutation."""


def validated_action(intent, observation, *, policy=None):
    if intent.get("type") != "BROWSER":
        raise ValueError("Only BROWSER intents can dispatch a tool.")
    matches = [item["action"] for item in observation if item["id"] == intent.get("target")]
    if len(matches) != 1:
        raise ValueError("Action must refer to exactly one fresh observed control.")
    action = matches[0]
    if policy is not None:
        policy.validate(action)
        return action
    if action.get("method") != "click" or action.get("arguments"):
        raise ValueError("Fixture permits only single observed clicks; arbitrary fill/navigation rejected.")
    return action
