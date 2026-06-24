import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import settings
from app.db.database import get_session_factory
from app.db.models import User

logger = logging.getLogger(__name__)


async def reaper_loop(app_state) -> None:
    """Background task: every 60 s stop containers idle past IDLE_TIMEOUT_MINUTES."""
    while True:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        try:
            await _reap_once(app_state)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reaper iteration failed")


async def _reap_once(app_state) -> None:
    container_manager = app_state.container_manager
    session_registry = app_state.session_registry
    port_pool = app_state.port_pool
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.idle_timeout_minutes)
    running = await container_manager.list_running()
    factory = get_session_factory(settings.db_path)

    for container in running:
        name = container.name
        if not name.startswith("gurobimcp-"):
            continue
        try:
            user_id = int(name.removeprefix("gurobimcp-"))
        except ValueError:
            continue

        async with factory() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user is None:
                continue

            last_used = user.last_used_at
            if last_used is None:
                continue  # container started but no chat completed yet — not idle

            if last_used.tzinfo is None:
                last_used = last_used.replace(tzinfo=timezone.utc)

            if last_used >= cutoff:
                continue

            port = user.assigned_port
            logger.info(
                "Reaping idle container %s for user %d (last_used=%s)", name, user_id, last_used
            )

            await session_registry.close_all_for_container(name)
            await container_manager.stop_container(user_id)
            if port is not None:
                await port_pool.release(port)

            user.assigned_port = None
            user.container_name = None
            await db.commit()
