"""Server-rendered HTML pages, plus HTML fragments for infinite scroll."""

import logging
from typing import Annotated, Literal
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

import models
from auth import OptionalUser, get_optional_user
from config import settings
from database import AsyncSessionLocal, get_db
from routers.comments import COMMENTS_PER_PAGE, get_comment_or_404, list_comments
from schemas import PostSort
from services import (
    PostFilters,
    annotate_posts,
    get_post_or_404,
    get_profile_stats,
    get_sidebar_data,
    get_user_or_404,
    list_posts,
)
from templating import templates

logger = logging.getLogger(__name__)

PostSource = Literal["all", "feed", "bookmarks", "user", "tag", "search"]

SORT_LABELS = {
    "newest": "Newest",
    "oldest": "Oldest",
    "popular": "Most liked",
    "relevance": "Best match",
}


async def load_page_context(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: OptionalUser,
) -> None:
    request.state.current_user = user
    request.state.sidebar = await get_sidebar_data(db)


async def ensure_page_context(request: Request) -> None:
    """For error pages rendered outside a page route (e.g. unknown URLs)."""
    if hasattr(request.state, "sidebar"):
        return
    request.state.current_user = None
    request.state.sidebar = None
    try:
        async with AsyncSessionLocal() as db:
            request.state.current_user = await get_optional_user(request, None, db)
            request.state.sidebar = await get_sidebar_data(db)
    except Exception:
        # The error page must still render if the database is the problem
        logger.exception("Could not load page context for error page")


def login_redirect(request: Request) -> RedirectResponse:
    next_url = request.url.path
    if request.url.query:
        next_url += f"?{request.url.query}"
    return RedirectResponse(
        f"/login?next={quote(next_url)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


router = APIRouter(include_in_schema=False, dependencies=[Depends(load_page_context)])

# Fragments don't render the sidebar, so they skip load_page_context
partials_router = APIRouter(prefix="/partials", include_in_schema=False)


async def render_post_list(
    request: Request,
    db: AsyncSession,
    viewer: models.User | None,
    *,
    template: str,
    title: str,
    source: PostSource,
    filters: PostFilters,
    sort_options: tuple[str, ...] = ("newest", "oldest", "popular"),
    empty_message: str = "No posts yet.",
    extra: dict | None = None,
):
    posts, total, has_more = await list_posts(
        db,
        filters,
        skip=0,
        limit=settings.posts_per_page,
    )
    await annotate_posts(db, posts, viewer)

    # Query string the infinite-scroll JS uses to fetch the next pages
    partial_params = {"source": source, "sort": filters.sort}
    if filters.q:
        partial_params["q"] = filters.q
    if filters.tag:
        partial_params["tag"] = filters.tag
    if filters.author_id is not None:
        partial_params["user_id"] = filters.author_id

    return templates.TemplateResponse(
        request,
        template,
        {
            "title": title,
            "posts": posts,
            "total": total,
            "has_more": has_more,
            "next_skip": len(posts),
            "partial_query": urlencode(partial_params),
            "sort": filters.sort,
            "sort_options": [(key, SORT_LABELS[key]) for key in sort_options],
            "empty_message": empty_message,
            **(extra or {}),
        },
    )


@router.get("/", name="home")
@router.get("/posts", name="posts")
async def home(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    sort: PostSort = "newest",
):
    return await render_post_list(
        request,
        db,
        viewer,
        template="home.html",
        title="Home",
        source="all",
        filters=PostFilters(sort=sort),
        empty_message="No posts yet. Be the first to write one!",
        extra={"active_tab": "all"},
    )


@router.get("/feed", name="feed_page")
async def feed_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
):
    if viewer is None:
        return login_redirect(request)
    return await render_post_list(
        request,
        db,
        viewer,
        template="home.html",
        title="Following",
        source="feed",
        filters=PostFilters(following_of=viewer.id),
        sort_options=(),
        empty_message=(
            "Posts from people you follow will show up here. "
            "Visit an author's profile and hit Follow to get started."
        ),
        extra={"active_tab": "feed"},
    )


@router.get("/search", name="search_page")
async def search_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    q: Annotated[str, Query(max_length=200)] = "",
    sort: PostSort = "relevance",
):
    q = q.strip()
    return await render_post_list(
        request,
        db,
        viewer,
        template="search.html",
        title=f"Search: {q}" if q else "Search",
        source="search",
        filters=PostFilters(q=q or None, sort=sort),
        sort_options=("relevance", "newest", "oldest", "popular"),
        empty_message=(
            f'No posts match "{q}". Try different keywords.'
            if q
            else "Type something in the search box to find posts."
        ),
        extra={"query": q},
    )


@router.get("/tags/{name}", name="tag_page")
async def tag_page(
    request: Request,
    name: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    sort: PostSort = "newest",
):
    name = name.lower()
    return await render_post_list(
        request,
        db,
        viewer,
        template="tag.html",
        title=f"#{name}",
        source="tag",
        filters=PostFilters(tag=name, sort=sort),
        empty_message=f"No posts tagged #{name} yet.",
        extra={"tag": name},
    )


@router.get("/bookmarks", name="bookmarks_page")
async def bookmarks_page(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
):
    if viewer is None:
        return login_redirect(request)
    return await render_post_list(
        request,
        db,
        viewer,
        template="bookmarks.html",
        title="Bookmarks",
        source="bookmarks",
        filters=PostFilters(bookmarked_by=viewer.id),
        sort_options=(),
        empty_message="You haven't saved any posts yet. Tap the bookmark icon on a post to save it for later.",
    )


@router.get("/users/{user_id}/posts", name="user_posts")
async def user_posts_page(
    request: Request,
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    sort: PostSort = "newest",
):
    user = await get_user_or_404(db, user_id)
    stats = await get_profile_stats(db, user, viewer)
    is_me = viewer is not None and viewer.id == user.id
    return await render_post_list(
        request,
        db,
        viewer,
        template="user_posts.html",
        title=f"{user.username}'s Posts",
        source="user",
        filters=PostFilters(author_id=user.id, sort=sort),
        empty_message=(
            "You haven't written any posts yet."
            if is_me
            else f"{user.username} hasn't posted anything yet."
        ),
        extra={"user": user, "stats": stats, "is_me": is_me},
    )


@router.get("/posts/{post_id}", name="post_page")
async def post_page(
    request: Request,
    post_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
):
    post = await get_post_or_404(db, post_id)
    await annotate_posts(db, [post], viewer)
    comments, comments_total, comments_has_more = await list_comments(
        db,
        post_id,
        skip=0,
        limit=COMMENTS_PER_PAGE,
    )
    return templates.TemplateResponse(
        request,
        "post.html",
        {
            "post": post,
            "title": post.title[:50],
            "comments": comments,
            "comments_total": comments_total,
            "comments_has_more": comments_has_more,
            "description": post.excerpt,
        },
    )


@router.get("/login", name="login_page")
async def login_page(request: Request, viewer: OptionalUser):
    if viewer is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"title": "Login"},
    )


@router.get("/register", name="register_page")
async def register_page(request: Request, viewer: OptionalUser):
    if viewer is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "register.html",
        {"title": "Register"},
    )




@router.get("/forgot-password", name="forgot_password_page")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request,
        "forgot_password.html",
        {"title": "Forgot Password"},
    )


@router.get("/reset-password", name="reset_password_page")
async def reset_password_page(request: Request):
    response = templates.TemplateResponse(
        request,
        "reset_password.html",
        {"title": "Reset Password"},
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/verify-email", name="verify_email_page")
async def verify_email_page(request: Request):
    response = templates.TemplateResponse(
        request,
        "verify_email.html",
        {"title": "Verify Email"},
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@partials_router.get("/posts")
async def posts_partial(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    source: PostSource = "all",
    skip: Annotated[int, Query(ge=0)] = 0,
    sort: PostSort = "newest",
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=30)] = None,
    user_id: int | None = None,
):
    """The next page of post cards, as HTML, for infinite scroll."""
    filters = PostFilters(sort=sort)
    if source == "feed" or source == "bookmarks":
        if viewer is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        if source == "feed":
            filters.following_of = viewer.id
        else:
            filters.bookmarked_by = viewer.id
    elif source == "user":
        filters.author_id = user_id
    elif source == "tag":
        filters.tag = tag.lower() if tag else None
    elif source == "search":
        filters.q = q

    posts, _, has_more = await list_posts(
        db,
        filters,
        skip=skip,
        limit=settings.posts_per_page,
    )
    await annotate_posts(db, posts, viewer)
    request.state.current_user = viewer

    response = templates.TemplateResponse(
        request,
        "partials/post_cards.html",
        {"posts": posts},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    response.headers["X-Next-Skip"] = str(skip + len(posts))
    return response


@partials_router.get("/posts/{post_id}/comments")
async def comments_partial(
    request: Request,
    post_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    skip: Annotated[int, Query(ge=0)] = 0,
):
    post = await get_post_or_404(db, post_id)
    comments, _, has_more = await list_comments(db, post_id, skip, COMMENTS_PER_PAGE)
    request.state.current_user = viewer
    response = templates.TemplateResponse(
        request,
        "partials/comments.html",
        {"comments": comments, "post": post},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    response.headers["X-Next-Skip"] = str(skip + len(comments))
    return response


@partials_router.get("/comments/{comment_id}", response_class=HTMLResponse)
async def comment_partial(
    request: Request,
    comment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
):
    comment = await get_comment_or_404(db, comment_id)
    request.state.current_user = viewer
    return templates.TemplateResponse(
        request,
        "partials/comments.html",
        {"comments": [comment], "post": comment.post},
    )
