"""Official MCP read/export boundary. Workspace OAuth token must be server-side.
Account activation remains external; never launches a trial or uses sponsor models.
"""

import asyncio
import json
import os
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    token = os.environ.get("CFO_MCP_ACCESS_TOKEN")
    if not token:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "code": "cfo_workspace_auth",
                    "action": "Authorize the existing cfo.ai workspace via official OAuth MCP after a no-charge account route is established.",
                    "endpoint": "https://api.cfo.ai/mcp",
                }
            )
        )
        return
    async with streamablehttp_client(
        "https://api.cfo.ai/mcp", headers={"Authorization": "Bearer " + token}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            available = await session.list_tools()
            orgs = await session.call_tool("list_orgs", {})
            output = {
                "status": "connected",
                "endpoint": "https://api.cfo.ai/mcp",
                "tools": [t.name for t in available.tools],
                "organizations": orgs.model_dump(mode="json"),
            }
            path = Path(os.environ.get("TRYFOLK_CFO_EXPORT", "runtime/cfo-mcp-export.json"))
            path.write_text(json.dumps(output, indent=2))
            print(json.dumps({"status": "connected", "export": str(path), "tools": output["tools"]}))


if __name__ == "__main__":
    asyncio.run(main())
