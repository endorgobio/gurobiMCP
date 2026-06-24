import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_secret
from app.core.security import get_current_user
from app.db.database import get_db
from app.db.models import User
from app.schemas.chat import ChatRequest, ChatResponse, FilePayload
from app.services.container_manager import PoolExhausted
from app.services.files import ensure_workspace, read_output_files, write_input_files
from app.services.mcp_client import call_tool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

_MAX_INPUT_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    port_pool = request.app.state.port_pool
    container_manager = request.app.state.container_manager
    session_registry = request.app.state.session_registry

    # ── 1. Ensure a container is running for this user ──────────────────────
    container_running = (
        current_user.container_name is not None
        and await container_manager.is_container_running(current_user.container_name)
    )

    if not container_running:
        try:
            port = await port_pool.acquire(current_user.id)
        except PoolExhausted:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No container slot available",
            )

        secret = decrypt_secret(current_user.encrypted_secret)
        try:
            container_name = await container_manager.start_container(
                current_user.id, port, current_user.access_id, secret
            )
        finally:
            del secret  # clear plaintext from memory ASAP

        if not await container_manager.poll_readiness(port):
            await container_manager.stop_container(current_user.id)
            await port_pool.release(port)
            # Clear any stale DB fields so the next request acquires a fresh port (T030)
            stale = (await db.execute(select(User).where(User.id == current_user.id))).scalar_one_or_none()
            if stale:
                stale.assigned_port = None
                stale.container_name = None
                await db.commit()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Container failed to start or Gurobi credentials rejected",
            )

        # Persist port/container assignment in DB (re-query to get the session-tracked instance)
        result = await db.execute(select(User).where(User.id == current_user.id))
        user = result.scalar_one()
        user.assigned_port = port
        user.container_name = container_name
        await db.commit()
    else:
        port = current_user.assigned_port
        container_name = current_user.container_name
        result = await db.execute(select(User).where(User.id == current_user.id))
        user = result.scalar_one()

    # ── 2. Validate and write input files ───────────────────────────────────
    if body.input_files:
        total_bytes = sum(len(f.content_b64) * 3 // 4 for f in body.input_files)
        if total_bytes > _MAX_INPUT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Input files exceed 25 MB limit",
            )

    ensure_workspace(current_user.id)  # ensure host directory exists
    work_dir = "/workspace"            # container-side mount path
    input_filenames: list[str] = (
        write_input_files(current_user.id, body.input_files) if body.input_files else []
    )

    # ── 3. Get or create the MCP session (enforces agent binding, T025 liveness) ──
    entry = await session_registry.get_or_create(
        user_id=current_user.id,
        conversation_id=body.conversation_id,
        agent=body.agent,
        port=port,
        container_name=container_name,
        container_manager=container_manager,
        access_id=current_user.access_id,
        encrypted_secret=current_user.encrypted_secret,
    )

    # ── 4. Call the tool; on transport error recover once (T026) ────────────
    try:
        async with entry.lock:
            text, output_filenames = await call_tool(
                entry.session,
                body.agent,
                body.prompt,
                input_filenames,
                work_dir,
            )
    except HTTPException:
        raise
    except (httpx.TransportError, ConnectionError, OSError) as exc:
        logger.info(
            "Transport error for user=%d conv=%s (%s: %s) — attempting recovery",
            current_user.id, body.conversation_id, type(exc).__name__, exc,
        )
        # Close stale registry entry (lock is no longer held — safe to call)
        await session_registry.close_session(current_user.id, body.conversation_id)

        # Restart container with fresh credentials
        secret = decrypt_secret(current_user.encrypted_secret)
        try:
            container_name = await container_manager.start_container(
                current_user.id, port, current_user.access_id, secret
            )
        finally:
            del secret

        if not await container_manager.poll_readiness(port):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Container failed to restart during recovery",
            )

        # Fresh session — recovered=True propagates into ChatResponse
        entry = await session_registry.get_or_create(
            user_id=current_user.id,
            conversation_id=body.conversation_id,
            agent=body.agent,
            port=port,
            container_name=container_name,
        )
        entry.recovered = True

        # Single retry
        async with entry.lock:
            text, output_filenames = await call_tool(
                entry.session,
                body.agent,
                body.prompt,
                input_filenames,
                work_dir,
            )

    # ── 5. Read output files and update last_used_at ─────────────────────────
    output_files: list[FilePayload] = (
        read_output_files(current_user.id, output_filenames) if output_filenames else []
    )

    user.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    return ChatResponse(
        conversation_id=body.conversation_id,
        agent=entry.agent,
        response=text,
        output_files=output_files,
        recovered=entry.recovered,
    )


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_conversation(
    conversation_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    session_registry = request.app.state.session_registry
    await session_registry.close_session(current_user.id, conversation_id)
