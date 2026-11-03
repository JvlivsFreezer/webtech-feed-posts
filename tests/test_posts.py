from conftest import create_post


async def test_create_post_with_tags_and_markdown(make_user):
    alice = await make_user("alice")
    post = await create_post(
        alice,
        title="Markdown Post",
        content="Hello **world**\n\n- one\n- two",
        tags=["Python", "#FastAPI", "python", " web dev "],
    )

    # Normalized, de-duplicated, and returned alphabetically
    assert post["tags"] == ["fastapi", "python", "web-dev"]
    assert "<strong>world</strong>" in post["content_html"]
    assert "<li>one</li>" in post["content_html"]
    assert post["excerpt"] == "Hello world one two"
    assert post["reading_time"] == 1
    assert post["likes"] == 0
    assert post["comments_count"] == 0
    assert post["image_path"] is None
    assert post["author"]["username"] == "alice"


async def test_markdown_is_sanitized(make_user):
    alice = await make_user("alice")
    post = await create_post(
        alice,
        content=(
            "<script>alert('xss')</script>\n\n"
            "[click](javascript:alert(1))\n\n"
            '<img src=x onerror="alert(1)">'
        ),
    )
    html = post["content_html"]
    # Raw HTML is shown as text, never executed
    assert "<script" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html
    # The javascript: link is left as plain text instead of becoming a link
    assert "href" not in html


async def test_invalid_tags_rejected(make_user):
    alice = await make_user("alice")
    response = await alice.post(
        "/api/posts",
        json={"title": "t", "content": "c", "tags": ["bad tag!"]},
    )
    assert response.status_code == 422

    response = await alice.post(
        "/api/posts",
        json={"title": "t", "content": "c", "tags": [f"t{i}" for i in range(6)]},
    )
    assert response.status_code == 422


async def test_title_length_validated(make_user):
    alice = await make_user("alice")
    response = await alice.post(
        "/api/posts",
        json={"title": "x" * 101, "content": "c"},
    )
    assert response.status_code == 422


async def test_long_posts_get_truncated_excerpt(make_user):
    alice = await make_user("alice")
    post = await create_post(alice, content="word " * 500)
    assert post["is_truncated"] is True
    assert post["excerpt"].endswith("…")
    assert len(post["excerpt"]) <= 281
    assert post["reading_time"] == 3


async def test_update_post_tags_and_content(make_user):
    alice = await make_user("alice")
    post = await create_post(alice, tags=["one", "two"])

    response = await alice.patch(
        f"/api/posts/{post['id']}",
        json={"tags": ["three"], "content": "Updated _content_"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["tags"] == ["three"]
    assert updated["title"] == "Hello World"
    assert "<em>content</em>" in updated["content_html"]

    response = await alice.put(
        f"/api/posts/{post['id']}",
        json={"title": "New title", "content": "New content"},
    )
    assert response.status_code == 200
    assert response.json()["tags"] == []


async def test_only_owner_can_edit(make_user):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)

    response = await bob.patch(f"/api/posts/{post['id']}", json={"title": "Hacked"})
    assert response.status_code == 403


async def test_only_owner_can_delete_post(make_user):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)

    assert (await bob.delete(f"/api/posts/{post['id']}")).status_code == 403
    assert (await alice.delete(f"/api/posts/{post['id']}")).status_code == 204
    assert (await alice.get(f"/api/posts/{post['id']}")).status_code == 404


async def test_list_posts_paginates(make_user, client):
    alice = await make_user("alice")
    for i in range(3):
        await create_post(alice, title=f"Post {i}")

    response = await client.get("/api/posts?limit=2")
    data = response.json()
    assert data["total"] == 3
    assert data["has_more"] is True
    assert [p["title"] for p in data["posts"]] == ["Post 2", "Post 1"]

    data = (await client.get("/api/posts?limit=2&skip=2")).json()
    assert [p["title"] for p in data["posts"]] == ["Post 0"]
    assert data["has_more"] is False


async def test_sort_by_oldest_and_popular(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    first = await create_post(alice, title="First")
    await create_post(alice, title="Second")
    third = await create_post(alice, title="Third")

    await bob.post(f"/api/posts/{first['id']}/like")
    await bob.post(f"/api/posts/{third['id']}/like")
    await alice.post(f"/api/posts/{third['id']}/like")

    titles = [p["title"] for p in (await client.get("/api/posts?sort=oldest")).json()["posts"]]
    assert titles == ["First", "Second", "Third"]

    titles = [p["title"] for p in (await client.get("/api/posts?sort=popular")).json()["posts"]]
    assert titles == ["Third", "First", "Second"]


async def test_full_text_search(make_user, client):
    alice = await make_user("alice")
    await create_post(alice, title="Async programming", content="Event loops and coroutines")
    await create_post(alice, title="Cooking pasta", content="Boil water, add salt")
    await create_post(alice, title="FastAPI tips", content="Dependencies are great")

    async def search(q: str) -> list[str]:
        response = await client.get("/api/posts", params={"q": q, "sort": "relevance"})
        return [p["title"] for p in response.json()["posts"]]

    # Matches content, with stemming ("coroutine" finds "coroutines")
    assert await search("coroutine") == ["Async programming"]
    # Partial words match titles
    assert await search("fast") == ["FastAPI tips"]
    # Web-search syntax: exclusions
    assert await search("water -salt") == []
    assert await search("nothing-matches-this") == []


async def test_filter_by_tag(make_user, client):
    alice = await make_user("alice")
    await create_post(alice, title="Tagged", tags=["python"])
    await create_post(alice, title="Untagged")

    data = (await client.get("/api/posts?tag=Python")).json()
    assert [p["title"] for p in data["posts"]] == ["Tagged"]

    tags = (await client.get("/api/tags")).json()
    assert tags == [{"name": "python", "post_count": 1}]


async def test_markdown_preview(make_user, client):
    alice = await make_user("alice")
    response = await alice.post("/api/posts/preview", json={"content": "# Title"})
    assert response.status_code == 200
    assert response.json()["html"].strip() == "<h1>Title</h1>"

    # Preview requires login
    assert (await client.post("/api/posts/preview", json={"content": "x"})).status_code == 401


async def test_require_email_verification_blocks_posting(make_user, monkeypatch):
    from config import settings

    alice = await make_user("alice")
    monkeypatch.setattr(settings, "require_email_verification", True)

    response = await alice.post("/api/posts", json={"title": "t", "content": "c"})
    assert response.status_code == 403
    assert "verify" in response.json()["detail"]
