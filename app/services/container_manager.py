import asyncio
import logging
from pathlib import Path

import docker
import docker.errors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

_IMAGE = "gurobi/mcp:latest"
_CONTAINER_PORT = 61095
_MCP_PATH = "/api/v1/agent/mcp"


class PoolExhausted(Exception):
    pass


class PortPool:
    def __init__(self, start: int, end: int) -> None:
        self._available: set[int] = set(range(start, end + 1))
        self._in_use: dict[int, int] = {}  # port -> user_id
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: int) -> int:
        async with self._lock:
            if not self._available:
                raise PoolExhausted("All container ports are in use")
            port = min(self._available)
            self._available.discard(port)
            self._in_use[port] = user_id
            return port

    async def release(self, port: int) -> None:
        async with self._lock:
            self._in_use.pop(port, None)
            self._available.add(port)

    def mark_in_use(self, port: int, user_id: int) -> None:
        """Called during startup reconciliation (not async — runs before serving requests)."""
        self._available.discard(port)
        self._in_use[port] = user_id


class ContainerManager:
    def __init__(self) -> None:
        self._client = docker.from_env()

    def _container_name(self, user_id: int) -> str:
        return f"gurobimcp-{user_id}"

    async def start_container(
        self, user_id: int, port: int, access_id: str, secret: str
    ) -> str:
        name = self._container_name(user_id)
        workspace = Path("data/workspaces") / str(user_id)
        workspace.mkdir(parents=True, exist_ok=True)

        await asyncio.to_thread(self._start_sync, name, port, access_id, secret, str(workspace.resolve()))
        logger.info("Started container %s on port %d", name, port)
        return name

    def _start_sync(
        self, name: str, port: int, access_id: str, secret: str, workspace_path: str
    ) -> None:
        # Remove any stale container with the same name
        try:
            old = self._client.containers.get(name)
            old.remove(force=True)
            logger.debug("Removed stale container %s", name)
        except docker.errors.NotFound:
            pass

        self._client.containers.run(
            image=_IMAGE,
            name=name,
            environment={
                "GRB_INTELLIGENCE_ACCESS_ID": access_id,
                "GRB_INTELLIGENCE_SECRET": secret,
                "GRB_MCP_MOUNT": "/workspace",
            },
            ports={f"{_CONTAINER_PORT}/tcp": ("127.0.0.1", port)},
            volumes={workspace_path: {"bind": "/workspace", "mode": "rw"}},
            detach=True,
            labels={"app": "gurobimcp"},
        )

    async def stop_container(self, user_id: int) -> None:
        name = self._container_name(user_id)
        await asyncio.to_thread(self._stop_sync, name)
        logger.info("Stopped container %s", name)

    def _stop_sync(self, name: str) -> None:
        try:
            container = self._client.containers.get(name)
            container.stop(timeout=5)
            container.remove()
        except docker.errors.NotFound:
            pass

    async def is_container_running(self, container_name: str) -> bool:
        return await asyncio.to_thread(self._is_running_sync, container_name)

    def _is_running_sync(self, container_name: str) -> bool:
        try:
            container = self._client.containers.get(container_name)
            return container.status == "running"
        except docker.errors.NotFound:
            return False

    async def poll_readiness(self, port: int, timeout: int = 20) -> bool:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=2.0
                )
                writer.close()
                await writer.wait_closed()
                logger.debug("Container on port %d is ready", port)
                return True
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
                await asyncio.sleep(1.0)
        logger.warning("Container on port %d did not become ready within %ds", port, timeout)
        return False

    async def list_running(self) -> list:
        """Return all running app=gurobimcp containers (used by reaper and reconciliation)."""
        return await asyncio.to_thread(
            lambda: self._client.containers.list(filters={"label": "app=gurobimcp", "status": "running"})
        )

    async def reconcile_port_pool(self, pool: "PortPool", db: AsyncSession) -> None:
        """
        On startup: mark ports of still-running containers as in-use in the pool
        and clear DB rows for containers that are no longer running.
        """
        from app.db.models import User

        running = await self.list_running()
        running_names = {c.name for c in running}

        for container in running:
            try:
                port_bindings = container.ports.get(f"{_CONTAINER_PORT}/tcp") or []
                if not port_bindings:
                    continue
                host_port = int(port_bindings[0]["HostPort"])
                user_id = int(container.name.removeprefix("gurobimcp-"))
                pool.mark_in_use(host_port, user_id)
                logger.info("Reconciled container %s on port %d", container.name, host_port)
            except (KeyError, ValueError, TypeError, IndexError):
                logger.warning("Could not reconcile container %s", container.name)

        # Clear stale DB references for containers no longer running
        result = await db.execute(select(User).where(User.container_name.isnot(None)))
        for user in result.scalars().all():
            if user.container_name not in running_names:
                logger.info("Clearing stale container ref for user %d (%s)", user.id, user.container_name)
                user.assigned_port = None
                user.container_name = None
        await db.commit()
