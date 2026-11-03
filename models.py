from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from database import Base
from rendering import excerpt, reading_time, render_markdown

EXCERPT_LENGTH = 280

# Association tables (no ORM class needed - rows are just links)
post_tags = Table(
    "post_tags",
    Base.metadata,
    Column(
        "post_id",
        ForeignKey("posts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)

post_likes = Table(
    "post_likes",
    Base.metadata,
    Column(
        "user_id",
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "post_id",
        ForeignKey("posts.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

bookmarks = Table(
    "bookmarks",
    Base.metadata,
    Column(
        "user_id",
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "post_id",
        ForeignKey("posts.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

follows = Table(
    "follows",
    Base.metadata,
    Column(
        "follower_id",
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "followed_id",
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint("follower_id <> followed_id", name="ck_follows_no_self_follow"),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    image_file: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        default=None,
    )
    bio: Mapped[str | None] = mapped_column(String(300), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )
    # Bumped to invalidate every token issued before (logout everywhere,
    # password change/reset)
    token_version: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )

    posts: Mapped[list[Post]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
    )

    comments: Mapped[list[Comment]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    reset_tokens: Mapped[list[PasswordResetToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    verification_tokens: Mapped[list[EmailVerificationToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def image_path(self) -> str:
        if self.image_file:
            return f"/media/profile_pics/{self.image_file}"
        return "/static/profile_pics/default.jpg"


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)

    posts: Mapped[list[Post]] = relationship(
        secondary=post_tags,
        back_populates="tags",
    )


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    date_posted: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True,
    )
    # Cached count of rows in post_likes, kept in sync by the like endpoints
    likes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    image_file: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        default=None,
    )
    # Maintained by Postgres - title matches rank above content matches
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(content, '')), 'B')",
            persisted=True,
        ),
        deferred=True,
    )

    author: Mapped[User] = relationship(back_populates="posts")

    tags: Mapped[list[Tag]] = relationship(
        secondary=post_tags,
        back_populates="posts",
        order_by="Tag.name",
    )

    comments: Mapped[list[Comment]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Per-request flags, set by services.annotate_posts for the current viewer
    # (left unannotated so SQLAlchemy doesn't treat them as columns)
    liked_by_me = False
    bookmarked_by_me = False

    @property
    def image_path(self) -> str | None:
        if self.image_file:
            return f"/media/post_images/{self.image_file}"
        return None

    @property
    def content_html(self) -> str:
        return render_markdown(self.content)

    @property
    def excerpt(self) -> str:
        return excerpt(self.content, EXCERPT_LENGTH)

    @property
    def is_truncated(self) -> bool:
        return self.excerpt.endswith("…")

    @property
    def reading_time(self) -> int:
        return reading_time(self.content)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    post: Mapped[Post] = relationship(back_populates="comments")
    author: Mapped[User] = relationship(back_populates="comments")


# Defined after Comment so the subquery can reference it
Post.comments_count = column_property(
    select(func.count(Comment.id))
    .where(Comment.post_id == Post.id)
    .correlate_except(Comment)
    .scalar_subquery(),
)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(back_populates="reset_tokens")


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(back_populates="verification_tokens")
