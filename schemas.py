import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MAX_TAGS_PER_POST = 5
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")

PostSort = Literal["newest", "oldest", "popular", "relevance"]


def normalize_tags(tags: list[str]) -> list[str]:
    """Lowercase, trim, strip a leading '#', drop duplicates (keeping order)."""
    cleaned: list[str] = []
    for raw in tags:
        tag = raw.strip().lstrip("#").lower().replace(" ", "-")
        if not tag:
            continue
        if not TAG_PATTERN.match(tag):
            msg = (
                f"Invalid tag '{raw}'. Tags may contain letters, numbers and "
                "dashes, up to 30 characters."
            )
            raise ValueError(msg)
        if tag not in cleaned:
            cleaned.append(tag)
    if len(cleaned) > MAX_TAGS_PER_POST:
        msg = f"A post can have at most {MAX_TAGS_PER_POST} tags"
        raise ValueError(msg)
    return cleaned


class UserBase(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email: EmailStr = Field(max_length=120)


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    image_file: str | None
    image_path: str
    bio: str | None = None
    created_at: datetime | None = None


class UserProfile(UserPublic):
    post_count: int = 0
    followers_count: int = 0
    following_count: int = 0
    total_likes: int = 0
    followed_by_me: bool = False


class UserPrivate(UserPublic):
    email: EmailStr
    email_verified: bool


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=120)
    bio: str | None = Field(default=None, max_length=300)


class Token(BaseModel):
    access_token: str
    token_type: str


class PostBase(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class PostCreate(PostBase):
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, tags: list[str]) -> list[str]:
        return normalize_tags(tags)


class PostUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1)
    tags: list[str] | None = None

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, tags: list[str] | None) -> list[str] | None:
        return None if tags is None else normalize_tags(tags)


class PostResponse(PostBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    date_posted: datetime
    author: UserPublic
    content_html: str
    excerpt: str
    is_truncated: bool
    reading_time: int
    image_path: str | None
    tags: list[str]
    likes: int
    comments_count: int
    liked_by_me: bool = False
    bookmarked_by_me: bool = False

    @field_validator("tags", mode="before")
    @classmethod
    def _tag_names(cls, tags: list) -> list[str]:
        return [tag if isinstance(tag, str) else tag.name for tag in tags]


class PaginatedPostsResponse(BaseModel):
    posts: list[PostResponse]
    total: int
    skip: int
    limit: int
    has_more: bool


class MarkdownPreviewRequest(BaseModel):
    content: str = Field(max_length=50_000)


class MarkdownPreviewResponse(BaseModel):
    html: str


class LikeResponse(BaseModel):
    liked: bool
    likes: int


class BookmarkResponse(BaseModel):
    bookmarked: bool


class FollowResponse(BaseModel):
    following: bool
    followers_count: int


class CommentCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, content: str) -> str:
        content = content.strip()
        if not content:
            msg = "Comment cannot be empty"
            raise ValueError(msg)
        return content


class CommentUpdate(CommentCreate):
    pass


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    post_id: int
    user_id: int
    created_at: datetime
    updated_at: datetime | None
    author: UserPublic


class PaginatedCommentsResponse(BaseModel):
    comments: list[CommentResponse]
    total: int
    skip: int
    limit: int
    has_more: bool


class TagWithCount(BaseModel):
    name: str
    post_count: int


class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(max_length=120)


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class VerifyEmailRequest(BaseModel):
    token: str
