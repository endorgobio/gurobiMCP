from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine(db_path: str):
    global _engine
    if _engine is None:
        url = f"sqlite+aiosqlite:///{db_path}"
        _engine = create_async_engine(url, echo=False)
    return _engine


def get_session_factory(db_path: str) -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        engine = _get_engine(db_path)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    from app.config import settings

    factory = get_session_factory(settings.db_path)
    async with factory() as session:
        yield session


async def init_db(db_path: str) -> None:
    engine = _get_engine(db_path)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
