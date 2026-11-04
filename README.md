# FastAPI Blog

A community blog built with FastAPI, async SQLAlchemy, PostgreSQL and Alembic.
Pages are rendered on the server with Jinja2 and Bootstrap 5, and enhanced with a
little vanilla JavaScript.

## Features

**Writing and reading**
- Posts written in Markdown (sanitized), with a live server-rendered preview
- Up to 5 tags per post, tag pages, and a popular-tags list
- An optional image per post (resized automatically)
- Excerpts with "Read more", reading time, and relative timestamps ("3 hours ago")
- Full-text search (Postgres `tsvector` + GIN index) with title substring matching
- Sort by newest, oldest, most liked, or best match
- Infinite scroll with skeleton loaders (the "Load more" button stays as a fallback)

**Community**
- Likes (one per user), bookmarks, and comments (edit/delete)
- Follow authors, plus a "Following" feed
- Profiles with avatar, bio, join date, and post/like/follower counts
- A sidebar with community stats, popular posts this week, latest posts, top
  authors, and tags

**Accounts and security**
- HttpOnly cookie auth with short-lived access tokens and silently refreshed
  refresh tokens; Bearer tokens still work for API clients and `/docs`
- CSRF protection: SameSite=Lax cookies plus a required `X-Requested-With`
  header on cookie-authenticated writes
- "Log out of all devices"; changing or resetting your password revokes other sessions
- Email verification (optionally required before posting/commenting) and password reset
- Rate limiting on login, registration and password/verification emails

**UI**
- Light/dark/auto theme, toasts instead of blocking modals, confirm dialogs
- Character counters and inline validation on forms
- Friendly empty states and error pages
- Accessibility: skip link, visible focus rings, `aria-live` announcements,
  WCAG AA contrast, and reduced-motion support

## Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and PostgreSQL.

1. Create a `.env` file:

   ```env
   DATABASE_URL=postgresql+psycopg://bloguser:blogpass@localhost/blog
   SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
   # Print emails (verification / password reset links) to the terminal
   # instead of sending them:
   MAIL_BACKEND=console
   ```

   Other settings (see `config.py`): `SITE_NAME`, `REQUIRE_EMAIL_VERIFICATION`,
   `RATE_LIMIT_ENABLED`, `COOKIE_SECURE` (set `true` in production behind HTTPS),
   `MAIL_SERVER` / `MAIL_PORT` / `MAIL_USERNAME` / `MAIL_PASSWORD` / `MAIL_FROM`, and
   `FRONTEND_URL`.

2. Install dependencies and create the database tables:

   ```bash
   uv sync
   uv run alembic upgrade head
   ```

3. Optionally load sample users, posts, tags, likes, comments and follows.
   Warning: this deletes existing data and profile pictures.

   ```bash
   uv run python populate_db.py
   ```

4. Run the app and open http://localhost:8000. API docs are at `/docs`.

   ```bash
   uv run uvicorn main:app --reload
   ```

   > **Windows:** psycopg's async driver can't run on Windows' default
   > `ProactorEventLoop`. `--reload` mode makes uvicorn use a selector event loop, so
   > use the command above.

## Troubleshooting

**"Can't reach the database"** (on every page, or when logging in/registering):
PostgreSQL isn't running. If you use Docker, start Docker Desktop and then the
container (e.g. `docker start <container-name>`), and reload the page.

## Tests

The tests run against a real PostgreSQL database, because they use full-text search,
`ON CONFLICT` and generated columns. By default the test database is your
`DATABASE_URL` with `_test` added to the database name, and it's created
automatically. Set `TEST_DATABASE_URL` to use a different one. Every run migrates the
database down to empty and back up, so the migrations get tested too.

```bash
uv run pytest
```

## Project layout

| Path | What's there |
| --- | --- |
| `main.py` | App setup, token-refresh and security-header middleware, error pages |
| `routers/` | `posts`, `comments`, `users`, `tags` (JSON API) and `pages` (HTML) |
| `services.py` | Query helpers shared by the API and pages (listing, search, sidebar…) |
| `models.py` / `schemas.py` | SQLAlchemy models / Pydantic schemas |
| `auth.py` | Password hashing, JWT cookies, auth dependencies |
| `rendering.py` | Markdown rendering, sanitizing, excerpts, reading time |
| `rate_limit.py` | Small in-memory rate limiter (use Redis if you run several workers) |
| `alembic/versions/` | Database migrations |
| `templates/` | Jinja2 templates (`_macros.html` holds the shared post card, forms…) |
| `static/js/app.js` | Likes, bookmarks, follows, infinite scroll, post form |
