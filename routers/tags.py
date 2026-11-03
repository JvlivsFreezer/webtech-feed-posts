from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import TagWithCount
from services import popular_tags

router = APIRouter()


@router.get("", response_model=list[TagWithCount])
async def get_tags(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Most-used tags first."""
    return await popular_tags(db, limit)
