import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import models
from database import engine
from rendering import excerpt, plain_text, reading_time, render_markdown
from schemas import normalize_tags


def test_render_markdown_basic_formatting():
    html = render_markdown("**bold** _em_ `code`\n\n> quote")
    assert "<strong>bold</strong>" in html
    assert "<em>em</em>" in html
    assert "<code>code</code>" in html
    assert "<blockquote>" in html


def test_single_newlines_become_line_breaks():
    # Old posts were plain text with line breaks, so keep those working
    assert "<br" in render_markdown("line one\nline two")


def test_links_get_safe_rel():
    html = render_markdown("[site](https://example.com)")
    assert 'href="https://example.com"' in html
    assert "nofollow" in html
    assert "noopener" in html


def test_plain_text_and_excerpt():
    assert plain_text("# Title\n\nSome *text* & more") == "Title Some text & more"
    assert excerpt("short", 280) == "short"
    long = excerpt("word " * 100, 50)
    assert long.endswith("…")
    assert len(long) <= 51


def test_reading_time():
    assert reading_time("hello") == 1
    assert reading_time("word " * 401) == 3


def test_normalize_tags():
    assert normalize_tags(["#Python", "python", "Web Dev", " "]) == ["python", "web-dev"]
    with pytest.raises(ValueError):
        normalize_tags(["no_underscores"])
    with pytest.raises(ValueError):
        normalize_tags(["a", "b", "c", "d", "e", "f"])


async def test_models_match_migrations():
    """Fails if a model changed without a matching Alembic migration."""

    def diff(sync_conn):
        return compare_metadata(MigrationContext.configure(sync_conn), models.Base.metadata)

    async with engine.connect() as conn:
        assert await conn.run_sync(diff) == []
