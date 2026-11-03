from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

engine = create_async_engine(
    settings.database_url,
    # Fail fast if Postgres is down. Without this, a stopped Docker container
    # can leave requests waiting forever (Docker still accepts the connection).
    connect_args={"connect_timeout": 5},
    # Test pooled connections before use, so the app recovers by itself after
    # the database restarts instead of erroring on stale connections
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
