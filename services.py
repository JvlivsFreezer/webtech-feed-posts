"""Query helpers shared by the JSON API and the server-rendered pages."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
from models import bookmarks, follows, post_likes, post_tags
from schemas import PostSort

POST_LOAD_OPTIONS = (selectinload(models.Post.author), selectinload(models.Post.tags))


@dataclass
class PostFilters:
    q: str | None = None
    tag: str | None = None
    author_id: int | None = None
    following_of: int | None = None  # posts by users this user follows
    bookmarked_by: int | None = None
    sort: PostSort = "newest"


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


async def list_posts(
    db: AsyncSession,
    filters: PostFilters,
    skip: int,
    limit: int,
) -> tuple[list[models.Post], int, bool]:
    """Return (posts, total, has_more) for one page of posts."""
    stmt = select(models.Post)
    tsquery = None

    if filters.q:
        tsquery = func.websearch_to_tsquery("english", filters.q)
        # Full-text search, plus a title substring match so partial words
        # like "fast" still find "FastAPI"
        stmt = stmt.where(
            or_(
                models.Post.search_vector.op("@@")(tsquery),
                models.Post.title.ilike(f"%{_escape_like(filters.q)}%"),
            ),
        )
    if filters.tag:
        stmt = stmt.where(models.Post.tags.any(models.Tag.name == filters.tag))
    if filters.author_id is not None:
        stmt = stmt.where(models.Post.user_id == filters.author_id)
    if filters.following_of is not None:
        followed_ids = select(follows.c.followed_id).where(
            follows.c.follower_id == filters.following_of,
        )
        stmt = stmt.where(models.Post.user_id.in_(followed_ids))
    if filters.bookmarked_by is not None:
        stmt = stmt.join(
            bookmarks,
            and_(
                bookmarks.c.post_id == models.Post.id,
                bookmarks.c.user_id == filters.bookmarked_by,
            ),
        )

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    newest = (models.Post.date_posted.desc(), models.Post.id.desc())
    if filters.bookmarked_by is not None and filters.sort == "newest":
        order_by = (bookmarks.c.created_at.desc(), models.Post.id.desc())
    elif filters.sort == "relevance" and tsquery is not None:
        order_by = (func.ts_rank(models.Post.search_vector, tsquery).desc(), *newest)
    elif filters.sort == "popular":
        order_by = (models.Post.likes.desc(), *newest)
    elif filters.sort == "oldest":
        order_by = (models.Post.date_posted.asc(), models.Post.id.asc())
    else:
        order_by = newest

    result = await db.execute(
        stmt.options(*POST_LOAD_OPTIONS)
        .order_by(*order_by)
        .offset(skip)
        .limit(limit),
    )
    posts = list(result.scalars().unique())
    return posts, total, skip + len(posts) < total


async def load_post(db: AsyncSession, post_id: int) -> models.Post | None:
    """Load a post with everything PostResponse needs, refreshing stale state."""
    result = await db.execute(
        select(models.Post)
        .options(*POST_LOAD_OPTIONS)
        .where(models.Post.id == post_id)
        .execution_options(populate_existing=True),
    )
    return result.scalars().first()


async def get_post_or_404(db: AsyncSession, post_id: int) -> models.Post:
    post = await load_post(db, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )
    return post


async def get_user_or_404(db: AsyncSession, user_id: int) -> models.User:
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


async def annotate_posts(
    db: AsyncSession,
    posts: Sequence[models.Post],
    viewer: models.User | None,
) -> None:
    """Set liked_by_me / bookmarked_by_me on each post for the current viewer."""
    if viewer is None or not posts:
        return

    post_ids = [post.id for post in posts]
    liked = set(
        await db.scalars(
            select(post_likes.c.post_id).where(
                post_likes.c.user_id == viewer.id,
                post_likes.c.post_id.in_(post_ids),
            ),
        ),
    )
    bookmarked = set(
        await db.scalars(
            select(bookmarks.c.post_id).where(
                bookmarks.c.user_id == viewer.id,
                bookmarks.c.post_id.in_(post_ids),
            ),
        ),
    )
    for post in posts:
        post.liked_by_me = post.id in liked
        post.bookmarked_by_me = post.id in bookmarked


async def get_or_create_tags(db: AsyncSession, names: list[str]) -> list[models.Tag]:
    if not names:
        return []
    # ON CONFLICT DO NOTHING keeps two concurrent posts with a new tag from
    # tripping the unique constraint
    await db.execute(
        insert(models.Tag)
        .values([{"name": name} for name in names])
        .on_conflict_do_nothing(index_elements=["name"]),
    )
    result = await db.execute(select(models.Tag).where(models.Tag.name.in_(names)))
    by_name = {tag.name: tag for tag in result.scalars()}
    return [by_name[name] for name in names]


async def popular_tags(db: AsyncSession, limit: int) -> list[dict]:
    result = await db.execute(
        select(models.Tag.name, func.count(post_tags.c.post_id).label("post_count"))
        .join(post_tags, post_tags.c.tag_id == models.Tag.id)
        .group_by(models.Tag.id)
        .order_by(func.count(post_tags.c.post_id).desc(), models.Tag.name)
        .limit(limit),
    )
    return [{"name": name, "post_count": count} for name, count in result]


async def get_profile_stats(
    db: AsyncSession,
    user: models.User,
    viewer: models.User | None,
) -> dict:
    post_count = (
        select(func.count())
        .select_from(models.Post)
        .where(models.Post.user_id == user.id)
        .scalar_subquery()
    )
    total_likes = (
        select(func.coalesce(func.sum(models.Post.likes), 0))
        .where(models.Post.user_id == user.id)
        .scalar_subquery()
    )
    followers = (
        select(func.count())
        .select_from(follows)
        .where(follows.c.followed_id == user.id)
        .scalar_subquery()
    )
    following = (
        select(func.count())
        .select_from(follows)
        .where(follows.c.follower_id == user.id)
        .scalar_subquery()
    )
    row = (await db.execute(select(post_count, total_likes, followers, following))).one()

    followed_by_me = False
    if viewer is not None and viewer.id != user.id:
        followed_by_me = (
            await db.scalar(
                select(follows.c.follower_id).where(
                    follows.c.follower_id == viewer.id,
                    follows.c.followed_id == user.id,
                ),
            )
        ) is not None

    return {
        "post_count": row[0],
        "total_likes": row[1],
        "followers_count": row[2],
        "following_count": row[3],
        "followed_by_me": followed_by_me,
    }


async def followers_count(db: AsyncSession, user_id: int) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(follows)
            .where(follows.c.followed_id == user_id),
        )
        or 0
    )


async def get_sidebar_data(db: AsyncSession) -> dict:
    recent = (
        await db.execute(
            select(models.Post.id, models.Post.title, models.Post.date_posted)
            .order_by(models.Post.date_posted.desc(), models.Post.id.desc())
            .limit(5),
        )
    ).all()

    week_ago = datetime.now(UTC) - timedelta(days=7)
    week_likes = func.count(post_likes.c.user_id)
    popular_this_week = (
        await db.execute(
            select(models.Post.id, models.Post.title, week_likes.label("likes"))
            .join(post_likes, post_likes.c.post_id == models.Post.id)
            .where(post_likes.c.created_at >= week_ago)
            .group_by(models.Post.id)
            .order_by(week_likes.desc(), models.Post.id.desc())
            .limit(5),
        )
    ).all()

    author_posts = func.count(models.Post.id)
    top_authors = [
        {
            "id": user.id,
            "username": user.username,
            "image_path": user.image_path,
            "post_count": count,
        }
        for user, count in (
            await db.execute(
                select(models.User, author_posts)
                .join(models.Post, models.Post.user_id == models.User.id)
                .group_by(models.User.id)
                .order_by(author_posts.desc(), models.User.username)
                .limit(5),
            )
        ).all()
    ]

    stats = (
        await db.execute(
            select(
                select(func.count()).select_from(models.User).scalar_subquery(),
                select(func.count()).select_from(models.Post).scalar_subquery(),
                select(func.count()).select_from(models.Comment).scalar_subquery(),
            ),
        )
    ).one()

    return {
        "recent": recent,
        "popular_this_week": popular_this_week,
        "top_authors": top_authors,
        "tags": await popular_tags(db, limit=12),
        "stats": {"users": stats[0], "posts": stats[1], "comments": stats[2]},
    }
