import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import HTTPException, status
from mcp import ClientSession

logger = logging.getLogger(__name__)


@dataclass
class SessionEntry:
    agent: str
    session: ClientSession
    exit_stack: AsyncExitStack
    container_name: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    recovered: bool = False


class SessionRegistry:
    """
    Maps (user_id, conversation_id) -> SessionEntry.

    Thread-safety: all mutations are serialized through _lock.
    anyio discipline (research R7): the registry lock is held while opening/closing
    sessions so that the same asyncio task enters and exits each AsyncExitStack.
    This blocks other registry operations briefly during session open — acceptable
    for v1 with tens of users.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[int, str], SessionEntry] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        user_id: int,
        conversation_id: str,
        agent: str,
        port: int,
        container_name: str,
        *,
        container_manager=None,
        access_id: str | None = None,
        encrypted_secret: str | None = None,
    ) -> SessionEntry:
        """
        Return the existing SessionEntry for this conversation, or open a new MCP
        session and create one.  Enforces agent immutability (FR-029/FR-030).

        T025: if container_manager is provided, validates container liveness before
        returning a cached entry.  If stale, restarts the container and opens a
        fresh session with recovered=True.
        """
        key = (user_id, conversation_id)
        recovering = False

        async with self._lock:
            entry = self._entries.get(key)

            if entry is not None:
                if entry.agent != agent:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Conversation '{conversation_id}' is already bound to agent "
                            f"'{entry.agent}'; cannot change to '{agent}'"
                        ),
                    )

                # T025: liveness check — is the backing container still running?
                if container_manager is not None:
                    if not await container_manager.is_container_running(entry.container_name):
                        logger.info(
                            "Stale session: user=%d conv=%s container=%s gone — recovering",
                            user_id, conversation_id, entry.container_name,
                        )
                        try:
                            await entry.exit_stack.aclose()
                        except Exception:
                            pass
                        del self._entries[key]
                        recovering = True
                        entry = None

            if entry is not None:
                entry.last_used_at = datetime.now(timezone.utc)
                return entry

            # Need a fresh session: either first time or recovering from stale container
            if recovering and container_manager is not None and encrypted_secret is not None:
                from app.core.crypto import decrypt_secret

                secret = decrypt_secret(encrypted_secret)
                try:
                    container_name = await container_manager.start_container(
                        user_id, port, access_id, secret
                    )
                finally:
                    del secret
                if not await container_manager.poll_readiness(port):
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Container failed to restart during session recovery",
                    )
                logger.info(
                    "Restarted container %s for recovery (user=%d conv=%s)",
                    container_name, user_id, conversation_id,
                )

            from app.services.mcp_client import open_mcp_session

            session, stack = await open_mcp_session(port)
            entry = SessionEntry(
                agent=agent,
                session=session,
                exit_stack=stack,
                container_name=container_name,
                recovered=recovering,
            )
            self._entries[key] = entry
            logger.info(
                "%s session: user=%d conv=%s agent=%s container=%s",
                "Recovered" if recovering else "New",
                user_id, conversation_id, agent, container_name,
            )
            return entry

    async def close_session(self, user_id: int, conversation_id: str) -> None:
        key = (user_id, conversation_id)
        async with self._lock:
            entry = self._entries.pop(key, None)
        if entry is not None:
            async with entry.lock:
                try:
                    await entry.exit_stack.aclose()
                except Exception as exc:
                    logger.debug("Error closing session %s/%s: %s", user_id, conversation_id, exc)

    async def close_all_for_container(self, container_name: str) -> None:
        """Close every session whose container matches — called by the reaper."""
        async with self._lock:
            to_close = [
                (k, e) for k, e in self._entries.items() if e.container_name == container_name
            ]
            for k, _ in to_close:
                del self._entries[k]

        for (uid, cid), entry in to_close:
            async with entry.lock:
                try:
                    await entry.exit_stack.aclose()
                except Exception as exc:
                    logger.debug("Error closing session %d/%s: %s", uid, cid, exc)

    async def close_all(self) -> None:
        """Graceful shutdown — close every open session."""
        async with self._lock:
            all_entries = list(self._entries.items())
            self._entries.clear()

        for (uid, cid), entry in all_entries:
            async with entry.lock:
                try:
                    await entry.exit_stack.aclose()
                except Exception as exc:
                    logger.debug("Error closing session %d/%s during shutdown: %s", uid, cid, exc)

    def entries_snapshot(self) -> list[tuple[tuple[int, str], SessionEntry]]:
        """Return a point-in-time snapshot of all entries (used by the reaper)."""
        return list(self._entries.items())
