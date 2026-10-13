"""Real TinyPerson cognition and episodic memory; bounded single external decision."""
# ruff: noqa: E402

import json
import sys
from pathlib import Path
from uuid import uuid4

protocol = sys.stdout
sys.stdout = sys.stderr  # TinyTroupe prints its disclaimer/config; keep protocol separate.
from tinytroupe import config_manager
from tinytroupe.clients import register_client, force_api_type
from tinytroupe.agent import TinyPerson, CognitiveActionModel, CognitiveActionModelWithReasoning
from tinytroupe.agent.action_generator import ActionGenerator
from tinytroupe.agent.mental_faculty import TinyMentalFaculty
from tinytroupe.factory import TinyPersonFactory
from tryfolk.codex_bridge import generate
from tryfolk.scenarios import generation_constraints
from llama_index.core import Settings as IndexSettings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from huggingface_hub import snapshot_download
from pydantic import BaseModel, Field

EMBEDDING_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
EMBEDDING_CACHE = Path(__file__).resolve().parents[2] / "runtime/embeddings"
EMBEDDING_FILES = [
    "config.json", "config_sentence_transformers.json", "model.safetensors", "modules.json",
    "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
    "vocab.txt", "1_Pooling/config.json",
]
embedding_snapshot = EMBEDDING_CACHE / "models--BAAI--bge-small-en-v1.5" / "snapshots" / EMBEDDING_REVISION
if not all((embedding_snapshot / file).is_file() for file in EMBEDDING_FILES):
    embedding_snapshot = Path(snapshot_download(
        "BAAI/bge-small-en-v1.5", revision=EMBEDDING_REVISION,
        cache_dir=str(EMBEDDING_CACHE), allow_patterns=EMBEDDING_FILES,
    ))
# Loading the complete pinned local snapshot preserves its CLS pooling and
# normalization modules. Remote './modules.json' lookups in SentenceTransformers
# can fail independently and silently select a different default architecture.
IndexSettings.embed_model = HuggingFaceEmbedding(
    model_name=str(embedding_snapshot),
    revision=EMBEDDING_REVISION,
    device="cpu",
    trust_remote_code=False,
    cache_folder=str(EMBEDDING_CACHE),
)


class PersonaSpec(BaseModel):
    name: str
    age: int = Field(ge=18, le=90)
    nationality: str
    residence: str
    occupation: str
    education: str
    style: str
    personality: str
    long_term_goals: list[str] = Field(max_length=6)
    preferences: list[str] = Field(max_length=6)
    beliefs: list[str] = Field(max_length=6)
    skills: list[str] = Field(max_length=6)
    behaviors: list[str] = Field(max_length=6)
    other_facts: list[str] = Field(max_length=6)


class ConsolidatedMemory(BaseModel):
    content: str
    type: str
    simulation_timestamp: str


class Consolidation(BaseModel):
    consolidation: list[ConsolidatedMemory]


config_manager.update_multiple(
    {
        "enable_memory_consolidation": True,
        "enable_continuous_contextual_semantic_memory_retrieval": True,
        "action_generator_enable_quality_checks": False,
    }
)


class CodexClient:
    def send_message(self, current_messages, response_format=None, **kwargs):
        if isinstance(response_format, dict) and response_format.get("type") == "json_object":
            spec = Consolidation if "consolidat" in json.dumps(current_messages).lower() else PersonaSpec
            value = generate(current_messages, spec.model_json_schema(), label="tinytroupe-generation")
            spec.model_validate(value)
        elif hasattr(response_format, "model_json_schema"):
            schema = response_format.model_json_schema()
            if response_format in (CognitiveActionModel, CognitiveActionModelWithReasoning):
                # The real SDK also defines DONE/THINK/TALK. Our single external
                # decision contract is narrower; constrain inference, never remap it.
                schema["$defs"]["Action"]["properties"]["type"]["enum"] = [
                    "BROWSER", "FINISH", "ABANDON"
                ]
            value = generate(current_messages, schema, label="tinytroupe")
            response_format.model_validate(value)
        else:
            # TinyTroupe's LLMChat handles its own typed JSON coercion. Preserve that
            # contract while Codex still emits a bounded outer schema.
            result = generate(
                current_messages,
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
                label="tinytroupe-text",
            )
            return {"role": "assistant", "content": result["text"]}
        return {"role": "assistant", "content": json.dumps(value)}

    def get_cost_stats(self):
        from tryfolk.codex_bridge import COST_STATS

        return dict(COST_STATS)

    def set_api_cache(self, enabled=False, **kwargs):
        if enabled:
            raise RuntimeError("Codex SDK callback caching is unsupported; no cross-persona cache.")


register_client("codex", CodexClient())
force_api_type("codex")


class BrowserFaculty(TinyMentalFaculty):
    def __init__(self):
        super().__init__("Browser")
        self.intent = None
        self.allowed = set()

    def actions_definitions_prompt(self):
        return "BROWSER: choose exactly one observed action by its ID in target; content explains your choice. FINISH: stop when goal met. ABANDON: leave because of frustration. Do not narrate execution; only the actual browser can execute."

    def actions_constraints_prompt(self):
        return "Only one BROWSER, FINISH or ABANDON action per fresh observation. BROWSER target must be in the latest observed list. Do not produce THINK, TALK or DONE. Never assume backend payment or entitlement state."

    def process_action(self, agent, action):
        if action["type"] not in ("BROWSER", "FINISH", "ABANDON"):
            raise RuntimeError("Persona returned an unsupported action; execution blocked.")
        if self.intent is not None:
            raise RuntimeError("Multiple external actions for one observation rejected.")
        if action["type"] == "BROWSER" and action["target"] not in self.allowed:
            raise RuntimeError("Persona referenced a stale or unobserved browser action.")
        self.intent = action
        return True


def build(persona):
    person = (
        TinyPerson.from_json(persona["initial_snapshot"], suppress=["_mental_faculties"])
        if persona.get("initial_snapshot")
        else TinyPerson(
            persona["name"],
            mental_faculties=[],
        )
    )
    person.action_generator = ActionGenerator(
        enable_quality_checks=False, enable_regeneration=False, enable_multi_action_output=False
    )
    for key, value in persona["profile"].items():
        person.define(key, value)
    faculty = BrowserFaculty()
    person.add_mental_faculties([faculty])
    for memory in persona.get("initial_semantic_memory", []):
        person.semantic_memory.store({"simulation_timestamp": None, **memory})
    retrieval = person.retrieve_relevant_memories("buy credits and use purchased capability", top_k=2)
    if not retrieval:
        raise RuntimeError("Real local semantic retrieval returned no initial memory.")
    return person, faculty


def main():
    person = faculty = None
    people = {}
    for line in sys.stdin:
        command = json.loads(line)
        try:
            operation = command["op"]
            bulk_key = None
            if operation.startswith("bulk_"):
                bulk_key = (command.get("run_id"), command.get("persona_id"))
                if not all(isinstance(value, str) and value for value in bulk_key):
                    raise RuntimeError("Persistent cognition requires exact run/persona identity.")
                command = {**command, "op": operation.removeprefix("bulk_")}
                if command["op"] == "release":
                    released, _ = people.pop(bulk_key)
                    TinyPerson.all_agents.pop(released.name, None)
                    print(json.dumps({"ok": True, "data": {"released": True}}), file=protocol, flush=True)
                    continue
                if command["op"] != "init":
                    if bulk_key not in people:
                        raise RuntimeError("Unknown run/persona memory; no cross-persona fallback.")
                    person, faculty = people[bulk_key]
            if command["op"] == "generate_bulk":
                from bulk_adapter import generate_bulk

                config_manager.update_multiple({"enable_memory_consolidation": False})
                data = generate_bulk(command, TinyPerson, generate)
            elif command["op"] == "generate":
                product_context = {k: v for k, v in command["brief"].items() if k != "generation_plan"}
                factory = TinyPersonFactory(context=json.dumps(product_context))
                factory.person_prompt_template_path = str(Path(__file__).with_name("person.mustache"))
                population = []
                goal = (
                    "buy the one-time Special Downloads art pack and use its protected artwork download"
                    if command["brief"].get("provider") == "dodo"
                    else "buy image credits and exercise the purchased image capability"
                )
                for index in range(command["count"]):
                    constraints = generation_constraints(command["brief"], index)
                    if constraints:
                        factory = TinyPersonFactory(
                            context=json.dumps(
                                {**product_context, "assigned_customer": constraints, "persona_index": index}
                            )
                        )
                        factory.person_prompt_template_path = str(Path(__file__).with_name("person.mustache"))
                    particulars = (
                        "Use these accepted constraints as descriptive customer data: "
                        + json.dumps(constraints)
                        if constraints
                        else f"A distinct synthetic customer #{index + 1}, with a clear goal to {goal}. Vary familiarity and patience. Only supported conditions: fresh account, desktop Chromium, en-US. No invented network/geography conditions."
                    )
                    persona_goal = constraints["scenario"]["customer_goal"] if constraints else goal
                    conditions = (
                        constraints["conditions"]
                        if constraints
                        else {"history": "new", "device": "desktop", "locale": "en-US"}
                    )
                    person = factory.generate_person(
                        agent_particularities=particulars,
                        attempts=1,
                    )
                    if person is None:
                        raise RuntimeError(
                            "TinyTroupe did not generate the requested population; no silent reduction."
                        )
                    semantic = [{"type": "information",
                                 "content": f"I intend to {persona_goal}. My supported initial conditions are {json.dumps(conditions, sort_keys=True)}. Hypothetical problems are task inputs, not observed outcomes."}]
                    challenge_fields = {}
                    if constraints and constraints.get("buyer_challenge"):
                        challenge_fields = {key: constraints[key] for key in ("buyer_challenge", "buyer_challenge_sha256")}
                        semantic[0]["content"] += " Buyer challenge: " + json.dumps(challenge_fields, sort_keys=True)
                        for memory in semantic:
                            person.semantic_memory.store({"simulation_timestamp": None, **memory})
                            person.listen(memory["content"])
                    spec = person.to_json()
                    population.append(
                        {
                            "id": uuid4().hex,
                            "index": index,
                            "name": person.name,
                            "profile": spec["persona"],
                            "conditions": conditions,
                            "memory": person.episodic_memory.retrieve_all() if challenge_fields else [],
                            "initial_semantic_memory": semantic,
                            **challenge_fields,
                            "initial_snapshot": person.to_json(),
                        }
                    )
                data = {"population": population}
            elif command["op"] == "prepare_intents":
                if people:
                    raise RuntimeError("Intent preparation cannot replace active journey memory.")
                from tryfolk.purchase_intents import prepare_intents

                data = {"population": prepare_intents(
                    command["population"], command["brief"], build, TinyPerson, generate,
                )}
            elif command["op"] == "init":
                if bulk_key and (bulk_key in people or len(people) >= 10 or command["persona"]["id"] != bulk_key[1]):
                    raise RuntimeError("Distinct at-most-ten owned persona memory sessions are required.")
                if bulk_key:
                    # Retain every actual episodic commit in this bulk service.
                    # Consolidation is disclosed as disabled, not simulated by a model.
                    config_manager.update_multiple({"enable_memory_consolidation": False})
                person, faculty = build(command["persona"])
                if bulk_key:
                    people[bulk_key] = person, faculty
                data = {
                    "sdk": "tinytroupe==0.7.0",
                    "memory_count": 0,
                    "embedding_model": "BAAI/bge-small-en-v1.5",
                    "embedding_revision": EMBEDDING_REVISION,
                    "semantic_retrieval": str(person.retrieve_relevant_memories("buy credits", top_k=2)),
                }
            elif command["op"] == "decide":
                faculty.intent = None
                faculty.allowed = {x["id"] for x in command["observation"]["actions"]}
                person.listen(json.dumps(command["observation"]))
                person.act(n=1, until_done=False, communication_display=False)
                if faculty.intent is None:
                    raise RuntimeError("No validated external decision produced.")
                data = {
                    "intent": faculty.intent,
                    "memory": person.episodic_memory.retrieve_all(),
                    "semantic_memory": person.semantic_memory.retrieve_all(),
                    "snapshot": person.to_json(),
                }
            elif command["op"] == "feedback":
                person.listen(json.dumps({"actual_browser_result": command["result"]}))
                data = {"memory_count": len(person.episodic_memory.retrieve_all())}
            elif command["op"] == "snapshot":
                data = {
                    "snapshot": person.to_json(),
                    "episodic_count": len(person.episodic_memory.retrieve_all()),
                    "semantic_memory": person.semantic_memory.retrieve_all(),
                    "cost_stats": CodexClient().get_cost_stats(),
                }
            elif command["op"] == "close":
                break
            else:
                raise RuntimeError("Unknown persona operation.")
            print(json.dumps({"ok": True, "data": data}, default=str), file=protocol, flush=True)
        except Exception as error:
            import traceback

            traceback.print_exc(file=sys.stderr)
            print(
                json.dumps({"ok": False, "error": type(error).__name__ + ": " + str(error)[:500],
                            "code": getattr(error, "code", None),
                            "model_invoked": getattr(error, "model_invoked", None)}),
                file=protocol,
                flush=True,
            )


if __name__ == "__main__":
    main()
