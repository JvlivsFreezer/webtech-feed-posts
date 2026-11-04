from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from PIL import UnidentifiedImageError
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

import models
from auth import (
    CurrentUser,
    OptionalUser,
    clear_auth_cookies,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    set_auth_cookies,
    verify_password,
)
from config import settings
from database import get_db
from email_utils import send_password_reset_email, send_verification_email
from image_utils import delete_post_image, delete_profile_image, process_profile_image
from models import follows, post_likes
from rate_limit import login_limiter, password_email_limiter, register_limiter
from routers.posts import Limit, Skip, paginated_posts
from schemas import (
    ChangePasswordRequest,
    FollowResponse,
    ForgotPasswordRequest,
    PaginatedPostsResponse,
    ResetPasswordRequest,
    Token,
    UserCreate,
    UserPrivate,
    UserProfile,
    UserPublic,
    UserUpdate,
    VerifyEmailRequest,
)
from services import (
    PostFilters,
    followers_count,
    get_profile_stats,
    get_user_or_404,
)

router = APIRouter()


async def start_email_verification(
    db: AsyncSession,
    user: models.User,
    background_tasks: BackgroundTasks,
) -> None:
    """Replace any pending verification token and email a new link."""
    await db.execute(
        sql_delete(models.EmailVerificationToken).where(
            models.EmailVerificationToken.user_id == user.id,
        ),
    )
    token = generate_reset_token()
    db.add(
        models.EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_reset_token(token),
            expires_at=datetime.now(UTC)
            + timedelta(hours=settings.verification_token_expire_hours),
        ),
    )
    await db.commit()

    background_tasks.add_task(
        send_verification_email,
        to_email=user.email,
        username=user.username,
        token=token,
    )


@router.post(
    "",
    response_model=UserPrivate,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_limiter)],
)
async def create_user(
    user: UserCreate,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(models.User).where(
            func.lower(models.User.username) == user.username.lower(),
        ),
    )
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    result = await db.execute(
        select(models.User).where(func.lower(models.User.email) == user.email.lower()),
    )
    existing_email = result.scalars().first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    new_user = models.User(
        username=user.username,
        email=user.email.lower(),
        password_hash=hash_password(user.password),
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    await start_email_verification(db, new_user, background_tasks)
    return new_user


@router.post(
    "/token",
    response_model=Token,
    dependencies=[Depends(login_limiter)],
)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    # Look up user by email (case-insensitive)
    # Note: OAuth2PasswordRequestForm uses "username" field, but we treat it as email
    result = await db.execute(
        select(models.User).where(
            func.lower(models.User.email) == form_data.username.lower(),
        ),
    )
    user = result.scalars().first()

    # Verify user exists and password is correct
    # Don't reveal which one failed (security best practice)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Browsers use the HttpOnly cookies; the token in the body is for API
    # clients and the /docs "Authorize" button
    access_token = set_auth_cookies(response, user)
    return Token(access_token=access_token, token_type="bearer")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    clear_auth_cookies(response)


@router.post("/me/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    response: Response,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Invalidate every session for this user, on every device."""
    current_user.token_version += 1
    await db.commit()
    clear_auth_cookies(response)


@router.get("/me", response_model=UserPrivate)
async def get_current_user(current_user: CurrentUser):
    return current_user


@router.get("/me/bookmarks", response_model=PaginatedPostsResponse)
async def get_my_bookmarks(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Skip = 0,
    limit: Limit = settings.posts_per_page,
):
    filters = PostFilters(bookmarked_by=current_user.id)
    return await paginated_posts(db, filters, skip, limit, current_user)


@router.post("/verify-email", status_code=status.HTTP_200_OK)
async def verify_email(
    request_data: VerifyEmailRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(models.EmailVerificationToken).where(
            models.EmailVerificationToken.token_hash
            == hash_reset_token(request_data.token),
        ),
    )
    token = result.scalars().first()

    if token is None or token.expires_at < datetime.now(UTC):
        if token is not None:
            await db.delete(token)
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification link",
        )

    await db.execute(
        update(models.User)
        .where(models.User.id == token.user_id)
        .values(email_verified=True),
    )
    await db.execute(
        sql_delete(models.EmailVerificationToken).where(
            models.EmailVerificationToken.user_id == token.user_id,
        ),
    )
    await db.commit()
    return {"message": "Email verified successfully."}


@router.post(
    "/me/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(password_email_limiter)],
)
async def resend_verification(
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your email is already verified",
        )
    await start_email_verification(db, current_user, background_tasks)
    return {"message": "Verification email sent."}


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(password_email_limiter)],
)
async def forgot_password(
    request_data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(models.User).where(
            func.lower(models.User.email) == request_data.email.lower(),
        ),
    )
    user = result.scalars().first()

    if user:
        await db.execute(
            sql_delete(models.PasswordResetToken).where(
                models.PasswordResetToken.user_id == user.id,
            ),
        )

        token = generate_reset_token()
        token_hash = hash_reset_token(token)
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.reset_token_expire_minutes,
        )

        reset_token = models.PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(reset_token)
        await db.commit()

        background_tasks.add_task(
            send_password_reset_email,
            to_email=user.email,
            username=user.username,
            token=token,
        )

    return {
        "message": "If an account exists with this email, you will receive password reset instructions.",
    }


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request_data: ResetPasswordRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    token_hash = hash_reset_token(request_data.token)

    result = await db.execute(
        select(models.PasswordResetToken).where(
            models.PasswordResetToken.token_hash == token_hash,
        ),
    )
    reset_token = result.scalars().first()

    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    if reset_token.expires_at < datetime.now(UTC):
        await db.delete(reset_token)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    result = await db.execute(
        select(models.User).where(models.User.id == reset_token.user_id),
    )
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    user.password_hash = hash_password(request_data.new_password)
    # Log out every existing session - whoever had the old password is out
    user.token_version += 1

    await db.execute(
        sql_delete(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == user.id,
        ),
    )

    await db.commit()
    return {
        "message": "Password reset successfully. You can now log in with your new password.",
    }


@router.patch("/me/password", status_code=status.HTTP_200_OK)
async def change_password(
    password_data: ChangePasswordRequest,
    response: Response,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not verify_password(password_data.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    current_user.password_hash = hash_password(password_data.new_password)
    # Sign out other devices, then re-issue cookies so this one stays logged in
    current_user.token_version += 1

    await db.execute(
        sql_delete(models.PasswordResetToken).where(
            models.PasswordResetToken.user_id == current_user.id,
        ),
    )

    await db.commit()
    set_auth_cookies(response, current_user)
    return {"message": "Password changed successfully"}


@router.get("/{user_id}", response_model=UserProfile)
async def get_user(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
):
    user = await get_user_or_404(db, user_id)
    stats = await get_profile_stats(db, user, viewer)
    return UserProfile(**UserPublic.model_validate(user).model_dump(), **stats)


@router.get("/{user_id}/posts", response_model=PaginatedPostsResponse)
async def get_user_posts(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    viewer: OptionalUser,
    skip: Skip = 0,
    limit: Limit = settings.posts_per_page,
):
    await get_user_or_404(db, user_id)
    filters = PostFilters(author_id=user_id)
    return await paginated_posts(db, filters, skip, limit, viewer)


async def _follow_list(
    db: AsyncSession,
    user_id: int,
    *,
    followers: bool,
    skip: int,
    limit: int,
) -> list[models.User]:
    await get_user_or_404(db, user_id)
    if followers:
        join_on, match = follows.c.follower_id, follows.c.followed_id
    else:
        join_on, match = follows.c.followed_id, follows.c.follower_id
    result = await db.execute(
        select(models.User)
        .join(follows, join_on == models.User.id)
        .where(match == user_id)
        .order_by(follows.c.created_at.desc())
        .offset(skip)
        .limit(limit),
    )
    return list(result.scalars())


@router.get("/{user_id}/followers", response_model=list[UserPublic])
async def get_followers(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Skip = 0,
    limit: Limit = 50,
):
    return await _follow_list(db, user_id, followers=True, skip=skip, limit=limit)


@router.get("/{user_id}/following", response_model=list[UserPublic])
async def get_following(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: Skip = 0,
    limit: Limit = 50,
):
    return await _follow_list(db, user_id, followers=False, skip=skip, limit=limit)


@router.post("/{user_id}/follow", response_model=FollowResponse)
async def follow_user(
    user_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't follow yourself",
        )
    await get_user_or_404(db, user_id)

    await db.execute(
        insert(follows)
        .values(follower_id=current_user.id, followed_id=user_id)
        .on_conflict_do_nothing(),
    )
    await db.commit()
    return FollowResponse(
        following=True,
        followers_count=await followers_count(db, user_id),
    )


@router.delete("/{user_id}/follow", response_model=FollowResponse)
async def unfollow_user(
    user_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    await get_user_or_404(db, user_id)
    await db.execute(
        sql_delete(follows).where(
            follows.c.follower_id == current_user.id,
            follows.c.followed_id == user_id,
        ),
    )
    await db.commit()
    return FollowResponse(
        following=False,
        followers_count=await followers_count(db, user_id),
    )


@router.patch("/{user_id}", response_model=UserPrivate)
async def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    user = current_user
    if (
        user_update.username is not None
        and user_update.username.lower() != user.username.lower()
    ):
        result = await db.execute(
            select(models.User).where(
                func.lower(models.User.username) == user_update.username.lower(),
            ),
        )
        existing_user = result.scalars().first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )

    email_changed = (
        user_update.email is not None
        and user_update.email.lower() != user.email.lower()
    )
    if email_changed:
        result = await db.execute(
            select(models.User).where(
                func.lower(models.User.email) == user_update.email.lower(),
            ),
        )
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

    if user_update.username is not None:
        user.username = user_update.username
    if user_update.email is not None:
        user.email = user_update.email.lower()
    if "bio" in user_update.model_fields_set:
        user.bio = (user_update.bio or "").strip() or None
    if email_changed:
        # The new address has to be confirmed again
        user.email_verified = False

    await db.commit()
    await db.refresh(user)

    if email_changed:
        await start_email_verification(db, user, background_tasks)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    response: Response,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this user",
        )

    user = current_user
    old_filename = user.image_file
    post_images = list(
        await db.scalars(
            select(models.Post.image_file).where(
                models.Post.user_id == user.id,
                models.Post.image_file.is_not(None),
            ),
        ),
    )

    # Their like rows are removed by ON DELETE CASCADE, so fix the cached
    # counts on the posts they liked first
    await db.execute(
        update(models.Post)
        .where(
            models.Post.id.in_(
                select(post_likes.c.post_id).where(post_likes.c.user_id == user.id),
            ),
        )
        .values(likes=models.Post.likes - 1),
    )

    await db.delete(user)
    await db.commit()
    clear_auth_cookies(response)

    if old_filename:
        delete_profile_image(old_filename)
    for filename in post_images:
        delete_post_image(filename)


@router.patch("/{user_id}/picture", response_model=UserPrivate)
async def upload_profile_picture(
    user_id: int,
    file: UploadFile,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user's picture",
        )

    content = await file.read()

    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {settings.max_upload_size_bytes // (1024 * 1024)}MB",
        )

    try:
        new_filename = await run_in_threadpool(process_profile_image, content)
    except UnidentifiedImageError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image file. Please upload a valid image (JPEG, PNG, GIF, WebP).",
        ) from err

    old_filename = current_user.image_file

    current_user.image_file = new_filename
    await db.commit()
    await db.refresh(current_user)

    if old_filename:
        delete_profile_image(old_filename)

    return current_user


@router.delete("/{user_id}/picture", response_model=UserPrivate)
async def delete_user_picture(
    user_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this user's picture",
        )

    old_filename = current_user.image_file

    if old_filename is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No profile picture to delete",
        )

    current_user.image_file = None
    await db.commit()
    await db.refresh(current_user)

    delete_profile_image(old_filename)

    return current_user
