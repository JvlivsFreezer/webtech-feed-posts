import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from auth import ACCESS_COOKIE, refresh_access_token_from_cookies
from config import settings
from database import engine
from routers import comments, pages, posts, tags, users
from templating import templates

logger = logging.getLogger(__name__)

DATABASE_DOWN_MESSAGE = (
    "Can't reach the database right now. If you're running this locally, "
    "make sure PostgreSQL (or its Docker container) is running, then try again."
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # Shutdown
    await engine.dispose()


app = FastAPI(lifespan=lifespan, title=settings.site_name)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/media", StaticFiles(directory="media"), name="media")

app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(comments.router, prefix="/api", tags=["comments"])
app.include_router(tags.router, prefix="/api/tags", tags=["tags"])
app.include_router(pages.router)
app.include_router(pages.partials_router)


@app.middleware("http")
async def refresh_access_cookie(request: Request, call_next):
    """
    Silently renew an expired access cookie from the refresh cookie, so the
    browser stays logged in without the frontend handling token refresh.
    """
    new_token = None
    if not request.url.path.startswith(("/static", "/media")):
        new_token = refresh_access_token_from_cookies(request)
        if new_token:
            request.state.access_token = new_token

    response = await call_next(request)

    if new_token:
        response.set_cookie(
            ACCESS_COOKIE,
            new_token,
            max_age=settings.access_token_expire_minutes * 60,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@app.exception_handler(StarletteHTTPException)
async def general_http_exception_handler(
    request: Request,
    exception: StarletteHTTPException,
):
    if request.url.path.startswith(("/api", "/partials")):
        return await http_exception_handler(request, exception)

    message = (
        exception.detail
        or "An error occurred. Please check your request and try again."
    )

    await pages.ensure_page_context(request)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exception.status_code,
            "title": exception.status_code,
            "message": message,
        },
        status_code=exception.status_code,
    )


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
@app.exception_handler(PoolTimeoutError)  # every pooled connection is stuck
async def database_unavailable_handler(request: Request, exception: Exception):
    """Database down / unreachable: say so clearly instead of a bare 500."""
    logger.error("Database unavailable: %s", exception)

    if request.url.path.startswith(("/api", "/partials")):
        return JSONResponse(
            {"detail": DATABASE_DOWN_MESSAGE},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Skip ensure_page_context - it would just hit the dead database again
    request.state.current_user = None
    request.state.sidebar = None
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status.HTTP_503_SERVICE_UNAVAILABLE,
            "title": status.HTTP_503_SERVICE_UNAVAILABLE,
            "message": DATABASE_DOWN_MESSAGE,
        },
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exception: RequestValidationError,
):
    if request.url.path.startswith(("/api", "/partials")):
        return await request_validation_exception_handler(request, exception)

    await pages.ensure_page_context(request)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "title": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "message": "Invalid request. Please check your input and try again.",
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )
