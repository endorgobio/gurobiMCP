import asyncio
import logging
logging.disable(logging.CRITICAL)  # silence the progress-notification warnings

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "http://localhost:61095/api/v1/agent/mcp"

async def main():
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # CHECK 1: do the tools DECLARE an output schema?
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"TOOL: {t.name}")
                print(f"  outputSchema: {getattr(t, 'outputSchema', 'ATTR_MISSING')}")
            print("-" * 50)

            # CHECK 2: does an actual call RETURN structured content?
            result = await session.call_tool(
                "gurobot",
                {"prompt": "What is a knapsack constraint?",
                 "inputFiles": None,
                 "currentDir": "/workspace"}
            )
            print("structuredContent:", result.structuredContent)
            print("isError:", result.isError)
            print("content block types:", [c.type for c in result.content])

asyncio.run(main())
