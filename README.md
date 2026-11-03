# Webtech — Feed and Posts

A runnable extraction of Abhinav Yadav's assigned feature areas from
[Ojas-Thakurela/webtech-project](https://github.com/Ojas-Thakurela/webtech-project).
Built with FastAPI, Jinja2, PostgreSQL and vanilla JavaScript.

## Included features

| Area | Pages and behavior | Main files |
| --- | --- | --- |
| Home feed | Latest posts, Following feed, sorting and infinite scroll | `routers/pages.py`, `services.py`, `templates/home.html`, `templates/post_list.html` |
| Discovery | Full-text search, tag pages, popular tags | `templates/search.html`, `templates/tag.html`, `routers/tags.py` |
| Bookmarks | Save/unsave posts and a personal saved-post list | `templates/bookmarks.html`, `routers/posts.py`, `routers/users.py` |
| Post editor | New/Edit Post modals, Markdown preview, tags and image uploads | `templates/layout.html`, `templates/_macros.html`, `templates/post.html`, `static/js/app.js` |
| Post detail | Full post, likes, bookmarks, comments with edit/delete and pagination | `templates/post.html`, `templates/partials/comments.html`, `routers/posts.py`, `routers/comments.py` |

New Post opens from the navigation bar after login. Edit Post opens on the
owner's post detail page. These preserve the source project's modal-based editor.

## Shared dependencies and scope

Authentication (register/login/logout, cookies, verification and password recovery),
read-only author pages and follow/unfollow are included as supporting code:
Following, bookmarks, editing, likes and comments need authenticated users.
The database models, migrations, shared layout, styles, image processing and
rendering utilities are also retained. These shared components are inherited
project infrastructure, not a claim of sole authorship.

Account settings, profile editing/uploads, account deletion and the account
password-change endpoint are excluded. Legacy finished-example files, seed scripts,
seed images and uploaded media are excluded as well. The shared schema and migration
chain are kept intact to avoid breaking relationships between posts and users.

## Run locally

Requires Python 3.12+, uv and PostgreSQL. Start a local database, for example:

```bash
docker run --name webtech-feed-posts-db \
  -e POSTGRES_USER=bloguser -e POSTGRES_PASSWORD=blogpass \
  -e POSTGRES_DB=blog -p 127.0.0.1:5432:5432 -d postgres:18
cp .env.example .env
```

Replace `SECRET_KEY` in `.env` with the output of:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Then install, migrate and run:

```bash
uv sync --locked
uv run alembic upgrade head
uv run uvicorn main:app --reload
```

Open <http://localhost:8000>, register two accounts, publish a post and follow
its author to try the Following feed. Verification/recovery emails print to the
terminal with `MAIL_BACKEND=console`. Interactive API docs are at `/docs`.

The Docker credentials above are for local development. Use your own database
credentials and set `COOKIE_SECURE=true` when serving over HTTPS.

## Tests

```bash
uv run pytest
```

Tests use a separate PostgreSQL database, defaulting to `DATABASE_URL` with
`_test` appended to the database name. The database user needs permission to
create it. `TEST_DATABASE_URL` can override the destination. Tests migrate that
database down/up and clear its tables; use a dedicated test database.

The suite covers post CRUD/ownership, search and tags, likes, bookmarks, comments,
following, page rendering, Markdown safety and authentication dependencies.

## Source and history

Extracted from source commit `24738217fb793eb07ee0b61a5a6028423a760c67`.
This repository starts a fresh history with descriptive feature messages, so
legacy tutorial-labelled commits are not imported. The source repository and
its history remain unchanged. Extraction commits describe packaging and adaptation
of existing project code, not original authorship of all included files.
