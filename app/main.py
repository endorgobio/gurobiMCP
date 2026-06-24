import asyncio
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db.database import get_session_factory, init_db

logger = logging.getLogger(__name__)

_SENSITIVE_PATHS = re.compile(r"^/(signup|login|chat)$")


class _SensitiveBodyFilter(logging.Filter):
    """Drop log records that leak credential field names (FR-005)."""

    _KEYWORDS = ("password", "gurobi_secret", "fernet_key", "jwt_secret", "content_b64")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        return not any(kw in msg for kw in self._KEYWORDS)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for handler in logging.root.handlers:
        handler.addFilter(_SensitiveBodyFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    logger.info("Starting up — initialising database at %s", settings.db_path)
    await init_db(settings.db_path)

    from app.services.container_manager import ContainerManager, PortPool
    from app.services.reaper import reaper_loop
    from app.services.session_registry import SessionRegistry

    app.state.port_pool = PortPool(settings.port_pool_start, settings.port_pool_end)
    app.state.container_manager = ContainerManager()
    app.state.session_registry = SessionRegistry()

    # Reconcile port pool against running containers so a restart doesn't orphan ports
    factory = get_session_factory(settings.db_path)
    async with factory() as db:
        await app.state.container_manager.reconcile_port_pool(app.state.port_pool, db)

    logger.info("Database ready; port pool %d–%d initialised", settings.port_pool_start, settings.port_pool_end)

    reaper_task = asyncio.create_task(reaper_loop(app.state))

    yield

    reaper_task.cancel()
    try:
        await reaper_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down — closing MCP sessions")
    await app.state.session_registry.close_all()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    from app.api.auth import router as auth_router
    from app.api.chat import router as chat_router

    app = FastAPI(
        title="Gurobi MCP Multi-User Backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(auth_router)
    app.include_router(chat_router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
