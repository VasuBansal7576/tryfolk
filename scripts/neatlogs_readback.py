"""Read existing genuine run trace once through the official authenticated MCP."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trace_id")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    load_dotenv(os.environ["TRYFOLK_NEATLOGS_ENV"])
    async with streamablehttp_client(
        "https://ingest.neatlogs.com/mcp",
        headers={"Authorization": "Bearer " + os.environ["NEATLOGS_API_KEY"]},
        timeout=20,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            available = await session.list_tools()
            tool = next(t for t in available.tools if t.name == "get_trace_context")
            schema = tool.inputSchema
            required = schema.get("required", [])
            if any(key != "trace_id" for key in required):
                raise RuntimeError("Unexpected trace readback parameters: " + json.dumps(schema))
            result = await session.call_tool("get_trace_context", {"trace_id": args.trace_id})
            output = {
                "trace_id": args.trace_id,
                "source": "official_neatlogs_mcp",
                "read_only": True,
                "result": result.model_dump(mode="json"),
            }
            Path(args.output).write_text(json.dumps(output, indent=2))
            print(
                json.dumps(
                    {
                        "trace_id": args.trace_id,
                        "readback_saved": args.output,
                        "isError": result.isError,
                        "content_types": [x.type for x in result.content],
                    }
                )
            )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        # Do not dump request objects or authorization headers on transport errors.
        def classify(value):
            if hasattr(value, "exceptions"):
                return [item for child in value.exceptions for item in classify(child)]
            response = getattr(value, "response", None)
            return [
                {
                    "type": type(value).__name__,
                    "http_status": response.status_code if response is not None else None,
                }
            ]

        print(
            json.dumps(
                {
                    "status": "blocked",
                    "finalized_readback": "not_verified",
                    "errors": classify(error),
                    "action": "Resolve upstream transport/account readiness, then read this existing trace again. No trace was resent.",
                }
            )
        )
        raise SystemExit(1)
