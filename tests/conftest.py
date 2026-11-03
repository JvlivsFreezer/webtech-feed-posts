"""
Test setup.

Tests run against a real PostgreSQL database (full-text search, ON CONFLICT and
generated columns are Postgres features). By default that's your DATABASE_URL
with "_test" appended to the database name; set TEST_DATABASE_URL to override.
The database is created if missing, and migrated from scratch every run.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Callable

import psycopg
import pytest
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url

from alembic import command
from config import settings

# Must happen before database.py creates the engine
_url = make_url(settings.database_url)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    _url.set(database=f"{_url.database}_test").render_as_string(hide_password=False),
)
settings.database_url = TEST_DATABASE_URL
settings.rate_limit_enabled = False
settings.mail_backend = "console"
settings.require_email_verification = False

import email_utils  # noqa: E402
import image_utils  # noqa: E402
import models  # noqa: E402
from main import app  # noqa: E402
from rate_limit import RateLimiter  # noqa: E402

PASSWORD = "password123"
CSRF = {"X-Requested-With": "pytest"}


def pytest_asyncio_loop_factories(config, item):
    # psycopg's async mode can't use Windows' default ProactorEventLoop
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


def _conninfo(database: str | None = None) -> str:
    """A plain libpq connection string for psycopg (no SQLAlchemy driver)."""
    url = make_url(TEST_DATABASE_URL).set(drivername="postgresql")
    if database:
        url = url.set(database=database)
    return url.render_as_string(hide_password=False)


def _create_database_if_missing() -> None:
    url = make_url(TEST_DATABASE_URL)
    with psycopg.connect(_conninfo("postgres"), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (url.database,),
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{url.database}"')


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Run every migration down and back up, so the migrations get tested too."""
    _create_database_if_missing()
    alembic_cfg = Config("alembic.ini")
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    # Sync connection so this also works around non-async tests
    yield
    tables = ", ".join(table.name for table in models.Base.metadata.sorted_tables)
    with psycopg.connect(_conninfo(), autocommit=True) as conn:
        conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    RateLimiter.reset_all()


@pytest.fixture(autouse=True)
def media_dirs(tmp_path, monkeypatch):
    """Keep uploaded test images out of the real media/ folder."""
    monkeypatch.setattr(image_utils, "PROFILE_PICS_DIR", tmp_path / "profile_pics")
    monkeypatch.setattr(image_utils, "POST_IMAGES_DIR", tmp_path / "post_images")
    return tmp_path


@pytest.fixture(autouse=True)
def sent_emails(monkeypatch) -> list[dict]:
    """Capture outgoing email instead of printing it."""
    sent: list[dict] = []

    async def fake_send_email(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(email_utils, "send_email", fake_send_email)
    return sent


def new_client(**kwargs) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        **kwargs,
    )


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """An anonymous browser."""
    async with new_client() as c:
        yield c


@pytest.fixture
async def make_user() -> AsyncIterator[Callable]:
    """
    Factory: register a user and return a logged-in client (cookie auth, with
    the CSRF header set). The user's JSON is on `client.user`.
    """
    clients: list[AsyncClient] = []

    async def _make(username: str = "alice") -> AsyncClient:
        c = new_client(headers=CSRF)
        clients.append(c)
        email = f"{username.lower()}@example.com"
        response = await c.post(
            "/api/users",
            json={"username": username, "email": email, "password": PASSWORD},
        )
        assert response.status_code == 201, response.text
        c.user = response.json()

        response = await c.post(
            "/api/users/token",
            data={"username": email, "password": PASSWORD},
        )
        assert response.status_code == 200, response.text
        c.token = response.json()["access_token"]
        return c

    yield _make
    for c in clients:
        await c.aclose()


async def create_post(client: AsyncClient, **overrides) -> dict:
    data = {"title": "Hello World", "content": "Some content", **overrides}
    response = await client.post("/api/posts", json=data)
    assert response.status_code == 201, response.text
    return response.json()
