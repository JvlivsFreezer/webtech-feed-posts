import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import models
from config import settings
from database import get_db

password_hash = PasswordHash.recommended()

# auto_error=False so requests can authenticate with the HttpOnly cookie instead.
# The Bearer header still works for the /docs "Authorize" button and API clients.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/users/token", auto_error=False)

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"

# Cookie-authenticated writes must send this header. Cross-site forms can't set
# custom headers, so this blocks CSRF on top of SameSite=Lax.
CSRF_HEADER = "X-Requested-With"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _encode_token(
    user_id: int,
    token_version: int,
    token_type: TokenType,
    expires_delta: timedelta,
) -> str:
    payload = {
        "sub": str(user_id),
        "ver": token_version,
        "type": token_type,
        "exp": datetime.now(UTC) + expires_delta,
    }
    return jwt.encode(
        payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )


def create_access_token(user_id: int, token_version: int) -> str:
    return _encode_token(
        user_id,
        token_version,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: int, token_version: int) -> str:
    return _encode_token(
        user_id,
        token_version,
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: TokenType) -> dict | None:
    """Return the token payload if it is valid and of the expected type."""
    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            options={"require": ["exp", "sub", "ver", "type"]},
        )
    except jwt.InvalidTokenError:
        return None
    if payload.get("type") != expected_type:
        return None
    return payload


def set_auth_cookies(response: Response, user: models.User) -> str:
    """Log the user in on this browser. Returns the access token."""
    access_token = create_access_token(user.id, user.token_version)
    refresh_token = create_refresh_token(user.id, user.token_version)
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return access_token


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE)


def refresh_access_token_from_cookies(request: Request) -> str | None:
    """
    If the access cookie is missing/expired but the refresh cookie is valid,
    mint a new access token. The caller stores it on the request and response.

    The new token keeps the refresh token's version, so a revoked session still
    fails in get_optional_user when the version is compared against the DB.
    """
    access = request.cookies.get(ACCESS_COOKIE)
    if access and decode_token(access, "access"):
        return None

    refresh = request.cookies.get(REFRESH_COOKIE)
    payload = decode_token(refresh, "refresh") if refresh else None
    if payload is None:
        return None

    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        return None
    return create_access_token(user_id, payload["ver"])


def _request_token(request: Request, bearer: str | None) -> tuple[str | None, bool]:
    """Return (token, came_from_cookie)."""
    if bearer:
        return bearer, False
    # A token refreshed by the middleware on this request takes priority
    token = getattr(request.state, "access_token", None) or request.cookies.get(
        ACCESS_COOKIE,
    )
    return token, token is not None


async def _load_user(token: str | None, db: AsyncSession) -> models.User | None:
    if not token:
        return None
    payload = decode_token(token, "access")
    if payload is None:
        return None
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        return None

    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if user is None or user.token_version != payload["ver"]:
        return None
    return user


async def get_optional_user(
    request: Request,
    bearer: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> models.User | None:
    """The logged-in user, or None."""
    token, _ = _request_token(request, bearer)
    return await _load_user(token, db)


async def get_current_user(
    request: Request,
    bearer: Annotated[str | None, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> models.User:
    token, from_cookie = _request_token(request, bearer)
    user = await _load_user(token, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if (
        from_cookie
        and request.method not in SAFE_METHODS
        and not request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing CSRF header",
        )

    return user


async def get_verified_user(
    current_user: Annotated[models.User, Depends(get_current_user)],
) -> models.User:
    if settings.require_email_verification and not current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address first",
        )
    return current_user


CurrentUser = Annotated[models.User, Depends(get_current_user)]
OptionalUser = Annotated[models.User | None, Depends(get_optional_user)]
VerifiedUser = Annotated[models.User, Depends(get_verified_user)]
