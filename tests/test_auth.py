from datetime import timedelta

from auth import ACCESS_COOKIE, REFRESH_COOKIE, _encode_token
from config import settings
from conftest import CSRF, PASSWORD, new_client


async def test_register_and_login_sets_httponly_cookies(client):
    response = await client.post(
        "/api/users",
        json={"username": "alice", "email": "Alice@Example.com", "password": PASSWORD},
    )
    assert response.status_code == 201
    user = response.json()
    assert user["email"] == "alice@example.com"
    assert user["email_verified"] is False
    assert "password_hash" not in user

    response = await client.post(
        "/api/users/token",
        data={"username": "alice@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"

    set_cookies = response.headers.get_list("set-cookie")
    access = next(c for c in set_cookies if c.startswith(f"{ACCESS_COOKIE}="))
    refresh = next(c for c in set_cookies if c.startswith(f"{REFRESH_COOKIE}="))
    for cookie in (access, refresh):
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie

    me = await client.get("/api/users/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


async def test_wrong_password_rejected(make_user, client):
    await make_user("alice")
    response = await client.post(
        "/api/users/token",
        data={"username": "alice@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401


async def test_duplicate_username_and_email_rejected(make_user, client):
    await make_user("alice")
    response = await client.post(
        "/api/users",
        json={"username": "ALICE", "email": "other@example.com", "password": PASSWORD},
    )
    assert response.status_code == 400
    response = await client.post(
        "/api/users",
        json={"username": "other", "email": "ALICE@example.com", "password": PASSWORD},
    )
    assert response.status_code == 400


async def test_bearer_token_still_works_for_api_clients(make_user):
    alice = await make_user("alice")
    async with new_client() as api:
        response = await api.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {alice.token}"},
        )
        assert response.status_code == 200
        # No CSRF header needed with a bearer token
        response = await api.post(
            "/api/posts",
            json={"title": "Via bearer", "content": "Hi"},
            headers={"Authorization": f"Bearer {alice.token}"},
        )
        assert response.status_code == 201


async def test_cookie_auth_requires_csrf_header_for_writes(make_user):
    alice = await make_user("alice")
    del alice.headers["X-Requested-With"]

    # Reads are fine without it
    assert (await alice.get("/api/users/me")).status_code == 200

    response = await alice.post("/api/posts", json={"title": "x", "content": "y"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing CSRF header"

    response = await alice.post(
        "/api/posts",
        json={"title": "x", "content": "y"},
        headers=CSRF,
    )
    assert response.status_code == 201


async def test_logout_clears_cookies(make_user):
    alice = await make_user("alice")
    response = await alice.post("/api/users/logout")
    assert response.status_code == 204
    assert (await alice.get("/api/users/me")).status_code == 401


async def test_expired_access_cookie_is_refreshed_silently(make_user):
    alice = await make_user("alice")
    # Simulate the 30-minute access token expiring
    alice.cookies.set(
        ACCESS_COOKIE,
        _encode_token(alice.user["id"], 0, "access", timedelta(seconds=-1)),
    )

    response = await alice.get("/api/users/me")
    assert response.status_code == 200
    assert any(
        c.startswith(f"{ACCESS_COOKIE}=") for c in response.headers.get_list("set-cookie")
    )


async def test_refresh_token_cannot_be_used_as_access_token(make_user):
    alice = await make_user("alice")
    refresh = alice.cookies.get(REFRESH_COOKIE)
    async with new_client() as api:
        response = await api.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {refresh}"},
        )
        assert response.status_code == 401


async def test_logout_all_revokes_every_session(make_user):
    alice = await make_user("alice")
    async with new_client(headers=CSRF) as other_device:
        await other_device.post(
            "/api/users/token",
            data={"username": "alice@example.com", "password": PASSWORD},
        )
        assert (await other_device.get("/api/users/me")).status_code == 200

        assert (await alice.post("/api/users/me/logout-all")).status_code == 204

        # Even the refresh cookie on the other device is now useless
        assert (await other_device.get("/api/users/me")).status_code == 401
    assert (await alice.get("/api/users/me")).status_code == 401






async def test_database_outage_gives_clear_error(client):
    from sqlalchemy.exc import OperationalError

    from database import get_db
    from main import app

    async def dead_db():
        raise OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))
        yield  # pragma: no cover - makes this a generator dependency like get_db

    app.dependency_overrides[get_db] = dead_db
    try:
        response = await client.post(
            "/api/users/token",
            data={"username": "alice@example.com", "password": PASSWORD},
        )
        assert response.status_code == 503
        assert "Can't reach the database" in response.json()["detail"]

        page = await client.get("/login")
        assert page.status_code == 503
        assert "Can&#39;t reach the database" in page.text
    finally:
        app.dependency_overrides.pop(get_db)


async def test_login_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    statuses = []
    for _ in range(11):
        response = await client.post(
            "/api/users/token",
            data={"username": "nobody@example.com", "password": "whatever"},
        )
        statuses.append(response.status_code)

    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429
    assert int(response.headers["Retry-After"]) > 0


async def test_forgot_and_reset_password_flow(make_user, client, sent_emails):
    alice = await make_user("alice")
    sent_emails.clear()

    response = await client.post(
        "/api/users/forgot-password",
        json={"email": "alice@example.com"},
    )
    assert response.status_code == 202
    assert len(sent_emails) == 1
    token = sent_emails[0]["plain_text"].split("token=")[1].split()[0]

    response = await client.post(
        "/api/users/reset-password",
        json={"token": token, "new_password": "brand-new-pass"},
    )
    assert response.status_code == 200

    # The reset logs out existing sessions
    assert (await alice.get("/api/users/me")).status_code == 401
    response = await client.post(
        "/api/users/token",
        data={"username": "alice@example.com", "password": "brand-new-pass"},
    )
    assert response.status_code == 200


async def test_forgot_password_does_not_reveal_unknown_emails(client, sent_emails):
    response = await client.post(
        "/api/users/forgot-password",
        json={"email": "nobody@example.com"},
    )
    assert response.status_code == 202
    assert sent_emails == []
