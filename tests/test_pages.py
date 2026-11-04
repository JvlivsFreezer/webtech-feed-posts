"""Server-rendered pages and HTML fragments."""

from conftest import create_post


async def test_home_page_lists_posts_with_real_sidebar(make_user, client):
    alice = await make_user("alice")
    post = await create_post(alice, title="Hello Sidebar", tags=["python"])
    await alice.post(f"/api/posts/{post['id']}/like")

    response = await client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "Hello Sidebar" in html
    # Sidebar sections with real data, not the old placeholder
    assert "Popular this week" in html
    assert "Top authors" in html
    assert "#python" in html
    assert "Our Sidebar" not in html


async def test_footer_no_longer_credits_tutorial_author(client):
    html = (await client.get("/")).text
    assert "Corey Schafer" not in html
    assert "API Docs" in html


async def test_logged_in_nav_is_server_rendered(make_user, client):
    new_post_button = 'data-bs-target="#createPostModal"'
    alice = await make_user("alice")
    html = (await alice.get("/")).text
    assert new_post_button in html
    assert 'data-action="logout"' in html
    assert '"currentUser": {"id": 1' in html

    anon = (await client.get("/")).text
    assert new_post_button not in anon
    assert 'data-action="logout"' not in anon
    assert '"currentUser": null' in anon


async def test_empty_home_shows_empty_state(client):
    html = (await client.get("/")).text
    assert "No posts yet" in html
    assert "empty-state" in html


async def test_post_page_renders_markdown_comments_and_owner_actions(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice, title="Full Post", content="Some **bold** text")
    await bob.post(f"/api/posts/{post['id']}/comments", json={"content": "Great read <b>!</b>"})

    html = (await alice.get(f"/posts/{post['id']}")).text
    assert "<strong>bold</strong>" in html
    assert "Great read &lt;b&gt;!&lt;/b&gt;" in html  # comments are escaped
    assert 'id="editModal"' in html
    assert 'id="deletePostBtn"' in html

    html = (await bob.get(f"/posts/{post['id']}")).text
    assert 'id="editModal"' not in html
    assert 'id="deletePostBtn"' not in html

    html = (await client.get(f"/posts/{post['id']}")).text
    assert "to join the conversation" in html


async def test_missing_post_shows_friendly_404(client):
    response = await client.get("/posts/12345")
    assert response.status_code == 404
    assert "Page not found" in response.text
    assert "Back home" in response.text


async def test_unknown_url_404_page_still_has_nav(make_user):
    alice = await make_user("alice")
    response = await alice.get("/definitely/not/a/page")
    assert response.status_code == 404
    assert "alice" in response.text  # logged-in nav renders on error pages


async def test_profile_page(make_user, client):
    alice = await make_user("alice")
    await alice.patch(f"/api/users/{alice.user['id']}", json={"bio": "Hello, I'm Alice"})
    await create_post(alice, title="Alice's Post")

    html = (await client.get(f"/users/{alice.user['id']}/posts")).text
    assert "Hello, I&#39;m Alice" in html
    assert "Joined" in html
    assert "Alice&#39;s Post" in html
    assert "Followers" in html


async def test_search_page(make_user, client):
    alice = await make_user("alice")
    await create_post(alice, title="Searchable thing")
    html = (await client.get("/search?q=searchable")).text
    assert "1 result for" in html
    assert "Searchable thing" in html

    html = (await client.get("/search?q=zzzz")).text
    assert "No posts match" in html


async def test_tag_page(make_user, client):
    alice = await make_user("alice")
    await create_post(alice, title="Tagged post", tags=["python"])
    html = (await client.get("/tags/python")).text
    assert "Tagged post" in html
    assert "1 post tagged #python" in html


async def test_login_required_pages_redirect(client):
    for path in ("/feed", "/bookmarks", "/account"):
        response = await client.get(path)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=")


async def test_admin_feature_is_gone(make_user):
    alice = await make_user("alice")
    assert (await alice.get("/admin")).status_code == 404
    assert (await alice.get("/api/admin/users")).status_code == 404
    assert "is_admin" not in (await alice.get("/api/users/me")).json()


async def test_login_page_redirects_when_logged_in(make_user):
    alice = await make_user("alice")
    response = await alice.get("/login")
    assert response.status_code == 303


async def test_posts_partial_for_infinite_scroll(make_user, client):
    alice = await make_user("alice")
    for i in range(12):
        await create_post(alice, title=f"Post {i}")

    html = (await client.get("/")).text
    assert 'id="loadMoreBtn"' in html
    assert "Post 11" in html
    assert "Post 1<" not in html

    response = await client.get("/partials/posts?source=all&sort=newest&skip=10")
    assert response.status_code == 200
    assert response.headers["X-Has-More"] == "false"
    assert response.headers["X-Next-Skip"] == "12"
    assert "Post 1" in response.text
    assert "Post 0" in response.text
    assert "<html" not in response.text


async def test_feed_partial_requires_login(client):
    response = await client.get("/partials/posts?source=feed")
    assert response.status_code == 401


async def test_verify_email_page_renders(client):
    response = await client.get("/verify-email?token=abc")
    assert response.status_code == 200
    assert response.headers["Referrer-Policy"] == "no-referrer"
