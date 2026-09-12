import os
from pathlib import Path

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

os.environ.setdefault("JWT_SECRET", "test-only-secret-never-use-in-production-123456789")
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

from app.api import rate_limit  # noqa: E402
from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402


@pytest_asyncio.fixture
async def db_factory(tmp_path: Path):
    url = os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_factory):
    async def override_db():
        async with db_factory() as db:
            yield db

    async def skip_limit():
        pass

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[rate_limit] = skip_limit
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()
