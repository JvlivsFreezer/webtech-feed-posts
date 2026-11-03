from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import models
from auth import CurrentUser, VerifiedUser
from database import get_db
from schemas import (
    CommentCreate,
    CommentResponse,
    CommentUpdate,
    PaginatedCommentsResponse,
)
from services import get_post_or_404

router = APIRouter()

COMMENTS_PER_PAGE = 20


async def list_comments(
    db: AsyncSession,
    post_id: int,
    skip: int,
    limit: int,
) -> tuple[list[models.Comment], int, bool]:
    total = (
        await db.scalar(
            select(func.count())
            .select_from(models.Comment)
            .where(models.Comment.post_id == post_id),
        )
        or 0
    )
    result = await db.execute(
        select(models.Comment)
        .options(selectinload(models.Comment.author))
        .where(models.Comment.post_id == post_id)
        .order_by(models.Comment.created_at, models.Comment.id)
        .offset(skip)
        .limit(limit),
    )
    comments = list(result.scalars())
    return comments, total, skip + len(comments) < total


async def get_comment_or_404(db: AsyncSession, comment_id: int) -> models.Comment:
    result = await db.execute(
        select(models.Comment)
        .options(
            selectinload(models.Comment.author),
            selectinload(models.Comment.post),
        )
        .where(models.Comment.id == comment_id)
        .execution_options(populate_existing=True),
    )
    comment = result.scalars().first()
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found",
        )
    return comment


@router.get("/posts/{post_id}/comments", response_model=PaginatedCommentsResponse)
async def get_comments(
    post_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = COMMENTS_PER_PAGE,
):
    await get_post_or_404(db, post_id)
    comments, total, has_more = await list_comments(db, post_id, skip, limit)
    return PaginatedCommentsResponse(
        comments=[CommentResponse.model_validate(c) for c in comments],
        total=total,
        skip=skip,
        limit=limit,
        has_more=has_more,
    )


@router.post(
    "/posts/{post_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_comment(
    post_id: int,
    comment: CommentCreate,
    current_user: VerifiedUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_post_or_404(db, post_id)
    new_comment = models.Comment(
        content=comment.content,
        post_id=post_id,
        user_id=current_user.id,
    )
    db.add(new_comment)
    await db.commit()
    return await get_comment_or_404(db, new_comment.id)


@router.patch("/comments/{comment_id}", response_model=CommentResponse)
async def update_comment(
    comment_id: int,
    comment_data: CommentUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    comment = await get_comment_or_404(db, comment_id)
    if comment.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to edit this comment",
        )

    comment.content = comment_data.content
    comment.updated_at = datetime.now(UTC)
    await db.commit()
    return await get_comment_or_404(db, comment_id)


@router.delete("/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    comment_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    comment = await get_comment_or_404(db, comment_id)

    # The comment's author or the post's author can remove it
    if current_user.id not in (comment.user_id, comment.post.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this comment",
        )

    await db.delete(comment)
    await db.commit()
