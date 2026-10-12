"""Codex client boundary with invented model I/O and the pinned SDK's JSON shape."""

import ast
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic import BaseModel
import pytest


class Action(BaseModel):
    type: str
    content: str
    target: str
    images: list[str] | None = None


class CognitiveState(BaseModel):
    goals: str
    context: list[str]
    attention: str
    emotions: str


class CognitiveActionModel(BaseModel):
    action: Action
    cognitive_state: CognitiveState


class CognitiveActionModelWithReasoning(CognitiveActionModel):
    reasoning: str


def client_with(generate):
    source = Path("integrations/tinytroupe/adapter.py")
    tree = ast.parse(source.read_text())
    client = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CodexClient")
    namespace = {
        "json": json,
        "generate": generate,
        "CognitiveActionModel": CognitiveActionModel,
        "CognitiveActionModelWithReasoning": CognitiveActionModelWithReasoning,
    }
    exec(compile(ast.Module(body=[client], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["CodexClient"]()


@pytest.mark.parametrize("response_format", [CognitiveActionModel, CognitiveActionModelWithReasoning])
def test_sdk_callback_can_only_choose_a_supported_browser_decision(response_format):
    def model(messages, schema, **kwargs):
        # A conforming callback chooses SDK-native DONE whenever the request allows it.
        value = {
            "action": {"type": "DONE", "target": "", "content": "No controls.", "images": None},
            "cognitive_state": {"goals": "buy", "context": [], "attention": "empty page", "emotions": "frustrated"},
        }
        if response_format is CognitiveActionModelWithReasoning:
            value["reasoning"] = "No permitted current controls."
        validator = Draft202012Validator(schema)
        if not validator.is_valid(value):
            value["action"]["type"] = "ABANDON"
        validator.validate(value)
        return value

    result = client_with(model).send_message([], response_format=response_format)
    assert json.loads(result["content"])["action"]["type"] == "ABANDON"


def test_all_three_external_types_are_valid_but_sdk_internal_actions_are_excluded():
    def model(messages, schema, **kwargs):
        validator = Draft202012Validator(schema)
        value = {"action": {"type": "BROWSER", "target": "fresh:0", "content": "Observe", "images": None},
                 "cognitive_state": {"goals": "buy", "context": [], "attention": "current", "emotions": "calm"}}
        for kind in ("BROWSER", "FINISH", "ABANDON"):
            value["action"]["type"] = kind
            assert validator.is_valid(value)
        for kind in ("DONE", "THINK", "TALK", "SHOW", "UNKNOWN"):
            value["action"]["type"] = kind
            assert not validator.is_valid(value)
        value["action"]["type"] = "FINISH"
        return value

    client_with(model).send_message([], response_format=CognitiveActionModel)
    # Do not change the actual library's general-purpose model.
    assert Draft202012Validator(CognitiveActionModel.model_json_schema()).is_valid({
        "action": {"type": "DONE", "target": "", "content": "", "images": None},
        "cognitive_state": {"goals": "buy", "context": [], "attention": "current", "emotions": "calm"},
    })


def test_other_sdk_response_schemas_are_preserved():
    def model(messages, schema, **kwargs):
        assert schema == CognitiveState.model_json_schema()
        return {"goals": "buy", "context": [], "attention": "current", "emotions": "calm"}

    client_with(model).send_message([], response_format=CognitiveState)
