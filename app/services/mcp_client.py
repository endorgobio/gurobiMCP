import logging
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

_MCP_URL_TEMPLATE = "http://127.0.0.1:{port}/api/v1/agent/mcp"


async def open_mcp_session(port: int) -> tuple[ClientSession, AsyncExitStack]:
    """
    Open a long-lived MCP ClientSession over streamable HTTP.

    Uses AsyncExitStack so the session survives across multiple requests (research R1).
    Caller is responsible for calling stack.aclose() when done.
    The stack must be closed from the same asyncio task that opened it (R7).
    """
    url = _MCP_URL_TEMPLATE.format(port=port)
    stack = AsyncExitStack()
    read, write, _ = await stack.enter_async_context(streamablehttp_client(url))
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    logger.debug("Opened MCP session at %s", url)
    return session, stack


async def call_tool(
    session: ClientSession,
    agent: str,
    prompt: str,
    input_files: list[str],
    work_dir: str,
) -> tuple[str, list[str]]:
    """
    Call one of the three Gurobi MCP tools (gurobot / explainer / modeler).

    Returns (text_response, output_filenames).
    The exact shape of outputFiles in the result is confirmed during implementation (research R8 TODO).
    """
    result = await session.call_tool(
        agent,
        {
            "prompt": prompt,
            "inputFiles": input_files or [],
            "currentDir": work_dir,
        },
    )

    text_parts: list[str] = []
    output_files: list[str] = []

    for content in result.content:
        if hasattr(content, "text"):
            text_parts.append(content.text)
        # Best-effort: look for outputFiles in structured data (confirm exact shape against real container)
        if hasattr(content, "data") and isinstance(content.data, dict):
            files = content.data.get("outputFiles", [])
            if isinstance(files, list):
                output_files.extend(str(f) for f in files)

    if result.isError:
        logger.warning("MCP tool %s returned isError=True", agent)

    return "\n".join(text_parts), output_files
