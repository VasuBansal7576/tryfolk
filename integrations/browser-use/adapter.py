"""Discover once with the real Browser Use Agent and an explicit Codex BaseChatModel."""

import asyncio
import json
import sys
from browser_use import Agent, BrowserSession, Tools
from browser_use.llm.views import ChatInvokeCompletion
from tryfolk.codex_bridge import agenerate


class CodexModel:
    model = "gpt-6.1-sol"
    provider = "codex-cli"
    name = "gpt-6.1-sol"
    model_name = "gpt-6.1-sol"
    _verified_api_keys = True

    async def ainvoke(self, messages, output_format=None, **kwargs):
        schema = (
            output_format.model_json_schema()
            if output_format
            else {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            }
        )
        text_messages = [m.model_dump(mode="json") for m in messages]
        for item in text_messages:
            if isinstance(item.get("content"), list):
                item["content"] = [x for x in item["content"] if x.get("type") == "text"]
        result = await agenerate(text_messages, schema, label="browser-use")
        return ChatInvokeCompletion(
            completion=output_format.model_validate(result) if output_format else result["text"], usage=None
        )


async def main():
    request = json.loads(sys.stdin.read())
    browser = BrowserSession(headless=True, allowed_domains=[request["url"]], user_data_dir=None)
    names = Tools().registry.registry.actions.keys()
    tools = Tools(
        exclude_actions=[
            name
            for name in names
            if name
            not in {
                "navigate",
                "done",
                "wait",
                "scroll",
                "extract",
                "find_text",
                "screenshot",
                "search_page",
                "find_elements",
            }
        ]
    )
    agent = Agent(
        task=f"Visit {request['url']} once and inspect the visible product and checkout promise. Do not click payment, login, signup or mutate any state. Return concise observed product, price, checkout control, expected capability and blockers. "
        + (
            "This is an owned licensed Dodo Test Mode target with a disclosed controlled fault; discovery does not verify payment or access."
            if request.get("provider") == "dodo"
            else "This is a local fixture, not Dodo payment evidence."
        ),
        llm=CodexModel(),
        browser_session=browser,
        tools=tools,
        use_vision=False,
        max_actions_per_step=1,
        max_failures=1,
        enable_cloud_sync=False,
    )
    try:
        history = await agent.run(max_steps=3)
        result = {
            "observed": history.final_result(),
            "actions": history.action_names(),
            "source_url": request["url"],
            "revision": request["revision"],
            "sdk": "browser-use==0.13.11",
            "measured_payment": False,
        }
        print(json.dumps(result), flush=True)
    finally:
        await browser.kill()


if __name__ == "__main__":
    asyncio.run(main())
