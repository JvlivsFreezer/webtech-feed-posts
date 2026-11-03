from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from PIL import UnidentifiedImageError
from sqlalchemy import delete as sql_delete
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

import models
from auth import CurrentUser, OptionalUser, VerifiedUser
from config import settings
from database import get_db
from image_utils import delete_post_image, process_post_image
from models import bookmarks, post_likes
from rendering import render_markdown
from schemas import (
    BookmarkResponse,
    LikeResponse,
    MarkdownPreviewRequest,
    MarkdownPreviewResponse,
    PaginatedPostsResponse,
    PostCreate,
    PostResponse,
    PostSort,
    PostUpdate,
)
from services import (
    PostFilters,
    annotate_posts,
    get_or_create_tags,
    get_post_or_404,
    list_posts,
    load_post,
)

router = APIRouter()

Skip = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


async def paginated_posts(
    db: AsyncSession,
    filters: PostFilters,
    skip: int,
    limit: int,
    viewer: models.User | None,
) -> PaginatedPostsResponse:
    posts, total, has_more = await list_posts(db, filters, skip, limit)
    await annotate_posts(db, posts, viewer)
    return PaginatedPostsResponse(
        posts=[PostResponse.model_validate(post) for post in posts],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more,
    )


def ensure_can_edit(post: models.Post, user: models.User) -> None:
    if post.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this post",
        )


@router.get("", response_model=PaginatedPostsResponse)
async def get_posts(
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    skip: Skip = 0,
    limit: Limit = settings.posts_per_page,
    q: Annotated[str | None, Query(max_length=200)] = None,
    tag: Annotated[str | None, Query(max_length=30)] = None,
    sort: PostSort = "newest",
):
    filters = PostFilters(q=q, tag=tag.lower() if tag else None, sort=sort)
    return await paginated_posts(db, filters, skip, limit, viewer)


@router.get("/feed", response_model=PaginatedPostsResponse)
async def get_feed(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Skip = 0,
    limit: Limit = settings.posts_per_page,
):
    """Posts from users the current user follows."""
    filters = PostFilters(following_of=current_user.id)
    return await paginated_posts(db, filters, skip, limit, current_user)


@router.post("/preview", response_model=MarkdownPreviewResponse)
async def preview_markdown(data: MarkdownPreviewRequest, _current_user: CurrentUser):
    return MarkdownPreviewResponse(html=render_markdown(data.content))


@router.post(
    "",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_post(
    post: PostCreate,
    current_user: VerifiedUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    new_post = models.Post(
        title=post.title,
        content=post.content,
        user_id=current_user.id,
        tags=await get_or_create_tags(db, post.tags),
    )
    db.add(new_post)
    await db.commit()
    return await load_post(db, new_post.id)


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
):
    post = await get_post_or_404(db, post_id)
    await annotate_posts(db, [post], viewer)
    return post


@router.put("/{post_id}", response_model=PostResponse)
async def update_post_full(
    post_id: int,
    post_data: PostCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)
    ensure_can_edit(post, current_user)

    post.title = post_data.title
    post.content = post_data.content
    post.tags = await get_or_create_tags(db, post_data.tags)

    await db.commit()
    post = await load_post(db, post_id)
    await annotate_posts(db, [post], current_user)
    return post


@router.patch("/{post_id}", response_model=PostResponse)
async def update_post_partial(
    post_id: int,
    post_data: PostUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)
    ensure_can_edit(post, current_user)

    update_data = post_data.model_dump(exclude_unset=True)
    tag_names = update_data.pop("tags", None)
    for field, value in update_data.items():
        setattr(post, field, value)
    if tag_names is not None:
        post.tags = await get_or_create_tags(db, tag_names)

    await db.commit()
    post = await load_post(db, post_id)
    await annotate_posts(db, [post], current_user)
    return post


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)

    if post.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this post",
        )

    image_file = post.image_file
    await db.delete(post)
    await db.commit()
    delete_post_image(image_file)


@router.post("/{post_id}/like", response_model=LikeResponse)
async def like_post(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)

    inserted = await db.scalar(
        insert(post_likes)
        .values(user_id=current_user.id, post_id=post.id)
        .on_conflict_do_nothing()
        .returning(post_likes.c.post_id),
    )
    likes = post.likes
    # Only bump the cached count when a new like row was actually created
    if inserted is not None:
        likes = await db.scalar(
            update(models.Post)
            .where(models.Post.id == post.id)
            .values(likes=models.Post.likes + 1)
            .returning(models.Post.likes),
        )
    await db.commit()
    return LikeResponse(liked=True, likes=likes)


@router.delete("/{post_id}/like", response_model=LikeResponse)
async def unlike_post(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)

    deleted = await db.scalar(
        sql_delete(post_likes)
        .where(
            post_likes.c.user_id == current_user.id,
            post_likes.c.post_id == post.id,
        )
        .returning(post_likes.c.post_id),
    )
    likes = post.likes
    if deleted is not None:
        likes = await db.scalar(
            update(models.Post)
            .where(models.Post.id == post.id)
            .values(likes=models.Post.likes - 1)
            .returning(models.Post.likes),
        )
    await db.commit()
    return LikeResponse(liked=False, likes=likes)


@router.post("/{post_id}/bookmark", response_model=BookmarkResponse)
async def bookmark_post(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)
    await db.execute(
        insert(bookmarks)
        .values(user_id=current_user.id, post_id=post.id)
        .on_conflict_do_nothing(),
    )
    await db.commit()
    return BookmarkResponse(bookmarked=True)


@router.delete("/{post_id}/bookmark", response_model=BookmarkResponse)
async def remove_bookmark(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)
    await db.execute(
        sql_delete(bookmarks).where(
            bookmarks.c.user_id == current_user.id,
            bookmarks.c.post_id == post.id,
        ),
    )
    await db.commit()
    return BookmarkResponse(bookmarked=False)


@router.patch("/{post_id}/image", response_model=PostResponse)
async def upload_post_image(
    post_id: int,
    file: UploadFile,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)
    ensure_can_edit(post, current_user)

    content = await file.read()

    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {settings.max_upload_size_bytes // (1024 * 1024)}MB",
        )

    try:
        new_filename = await run_in_threadpool(process_post_image, content)
    except UnidentifiedImageError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image file. Please upload a valid image (JPEG, PNG, GIF, WebP).",
        ) from err

    old_filename = post.image_file
    post.image_file = new_filename
    await db.commit()
    delete_post_image(old_filename)

    post = await load_post(db, post_id)
    await annotate_posts(db, [post], current_user)
    return post


@router.delete("/{post_id}/image", response_model=PostResponse)
async def delete_post_image_endpoint(
    post_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    post = await get_post_or_404(db, post_id)
    ensure_can_edit(post, current_user)

    old_filename = post.image_file
    if old_filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This post has no image",
        )

    post.image_file = None
    await db.commit()
    delete_post_image(old_filename)

    post = await load_post(db, post_id)
    await annotate_posts(db, [post], current_user)
    return post
