"""Likes, bookmarks, comments and follows."""

from conftest import create_post


async def test_like_is_idempotent_and_counted(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)

    response = await bob.post(f"/api/posts/{post['id']}/like")
    assert response.json() == {"liked": True, "likes": 1}
    # Liking twice doesn't double count
    response = await bob.post(f"/api/posts/{post['id']}/like")
    assert response.json() == {"liked": True, "likes": 1}

    await alice.post(f"/api/posts/{post['id']}/like")
    assert (await client.get(f"/api/posts/{post['id']}")).json()["likes"] == 2

    response = await bob.delete(f"/api/posts/{post['id']}/like")
    assert response.json() == {"liked": False, "likes": 1}
    response = await bob.delete(f"/api/posts/{post['id']}/like")
    assert response.json() == {"liked": False, "likes": 1}


async def test_liked_and_bookmarked_flags_are_per_viewer(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)
    await bob.post(f"/api/posts/{post['id']}/like")
    await bob.post(f"/api/posts/{post['id']}/bookmark")

    bobs_view = (await bob.get("/api/posts")).json()["posts"][0]
    assert bobs_view["liked_by_me"] is True
    assert bobs_view["bookmarked_by_me"] is True

    alices_view = (await alice.get(f"/api/posts/{post['id']}")).json()
    assert alices_view["liked_by_me"] is False

    anonymous_view = (await client.get(f"/api/posts/{post['id']}")).json()
    assert anonymous_view["liked_by_me"] is False


async def test_like_requires_login(make_user, client):
    alice = await make_user("alice")
    post = await create_post(alice)
    assert (await client.post(f"/api/posts/{post['id']}/like")).status_code == 401


async def test_like_missing_post_is_404(make_user):
    alice = await make_user("alice")
    assert (await alice.post("/api/posts/999/like")).status_code == 404


async def test_bookmarks_list_newest_first(make_user):
    alice = await make_user("alice")
    first = await create_post(alice, title="First")
    second = await create_post(alice, title="Second")

    await alice.post(f"/api/posts/{second['id']}/bookmark")
    await alice.post(f"/api/posts/{first['id']}/bookmark")

    data = (await alice.get("/api/users/me/bookmarks")).json()
    assert [p["title"] for p in data["posts"]] == ["First", "Second"]

    await alice.delete(f"/api/posts/{first['id']}/bookmark")
    data = (await alice.get("/api/users/me/bookmarks")).json()
    assert [p["title"] for p in data["posts"]] == ["Second"]


async def test_deleting_user_fixes_like_counts(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)
    await bob.post(f"/api/posts/{post['id']}/like")

    assert (await bob.delete(f"/api/users/{bob.user['id']}")).status_code == 204
    assert (await client.get(f"/api/posts/{post['id']}")).json()["likes"] == 0


async def test_deleting_user_removes_their_posts_and_comments(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    alices_post = await create_post(alice, tags=["python"])
    await bob.post(f"/api/posts/{alices_post['id']}/comments", json={"content": "Nice"})
    await alice.post(f"/api/users/{bob.user['id']}/follow")

    assert (await bob.delete(f"/api/users/{bob.user['id']}")).status_code == 204
    assert (await client.get(f"/api/posts/{alices_post['id']}")).json()["comments_count"] == 0

    assert (await alice.delete(f"/api/users/{alice.user['id']}")).status_code == 204
    assert (await client.get(f"/api/posts/{alices_post['id']}")).status_code == 404


async def test_comments_crud(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)
    url = f"/api/posts/{post['id']}/comments"

    response = await bob.post(url, json={"content": "  First!  "})
    assert response.status_code == 201
    comment = response.json()
    assert comment["content"] == "First!"
    assert comment["author"]["username"] == "bob"
    assert comment["updated_at"] is None

    await alice.post(url, json={"content": "Thanks"})

    data = (await client.get(url)).json()
    assert data["total"] == 2
    assert [c["content"] for c in data["comments"]] == ["First!", "Thanks"]
    assert (await client.get(f"/api/posts/{post['id']}")).json()["comments_count"] == 2

    # Only the author can edit
    edit_url = f"/api/comments/{comment['id']}"
    assert (await alice.patch(edit_url, json={"content": "Edited"})).status_code == 403
    response = await bob.patch(edit_url, json={"content": "Edited"})
    assert response.status_code == 200
    assert response.json()["content"] == "Edited"
    assert response.json()["updated_at"] is not None


async def test_blank_or_huge_comments_rejected(make_user):
    alice = await make_user("alice")
    post = await create_post(alice)
    url = f"/api/posts/{post['id']}/comments"
    assert (await alice.post(url, json={"content": "   "})).status_code == 422
    assert (await alice.post(url, json={"content": "x" * 2001})).status_code == 422


async def test_comment_deletion_permissions(make_user):
    alice = await make_user("alice")
    bob = await make_user("bob")
    carol = await make_user("carol")
    post = await create_post(alice)
    url = f"/api/posts/{post['id']}/comments"

    ids = [(await bob.post(url, json={"content": f"c{i}"})).json()["id"] for i in range(2)]

    # A random user can't delete it
    assert (await carol.delete(f"/api/comments/{ids[0]}")).status_code == 403
    # The comment author and the post author can
    assert (await bob.delete(f"/api/comments/{ids[0]}")).status_code == 204
    assert (await alice.delete(f"/api/comments/{ids[1]}")).status_code == 204
    assert (await bob.delete(f"/api/comments/{ids[1]}")).status_code == 404


async def test_follow_and_feed(make_user):
    alice = await make_user("alice")
    bob = await make_user("bob")
    carol = await make_user("carol")
    await create_post(bob, title="From Bob")
    await create_post(carol, title="From Carol")

    response = await alice.post(f"/api/users/{bob.user['id']}/follow")
    assert response.json() == {"following": True, "followers_count": 1}
    # Following twice is a no-op
    response = await alice.post(f"/api/users/{bob.user['id']}/follow")
    assert response.json()["followers_count"] == 1

    feed = (await alice.get("/api/posts/feed")).json()
    assert [p["title"] for p in feed["posts"]] == ["From Bob"]

    followers = (await alice.get(f"/api/users/{bob.user['id']}/followers")).json()
    assert [u["username"] for u in followers] == ["alice"]
    following = (await alice.get(f"/api/users/{alice.user['id']}/following")).json()
    assert [u["username"] for u in following] == ["bob"]

    response = await alice.delete(f"/api/users/{bob.user['id']}/follow")
    assert response.json() == {"following": False, "followers_count": 0}
    assert (await alice.get("/api/posts/feed")).json()["posts"] == []


async def test_cannot_follow_yourself(make_user):
    alice = await make_user("alice")
    response = await alice.post(f"/api/users/{alice.user['id']}/follow")
    assert response.status_code == 400


async def test_profile_stats(make_user, client):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)
    await create_post(alice)
    await bob.post(f"/api/posts/{post['id']}/like")
    await bob.post(f"/api/users/{alice.user['id']}/follow")

    profile = (await bob.get(f"/api/users/{alice.user['id']}")).json()
    assert profile["post_count"] == 2
    assert profile["total_likes"] == 1
    assert profile["followers_count"] == 1
    assert profile["following_count"] == 0
    assert profile["followed_by_me"] is True

    anonymous = (await client.get(f"/api/users/{alice.user['id']}")).json()
    assert anonymous["followed_by_me"] is False
    assert "email" not in anonymous


async def test_update_bio(make_user):
    alice = await make_user("alice")
    response = await alice.patch(
        f"/api/users/{alice.user['id']}",
        json={"bio": "  I write about Python.  "},
    )
    assert response.status_code == 200
    assert response.json()["bio"] == "I write about Python."

    # Updating another field leaves the bio alone; an empty bio clears it
    response = await alice.patch(f"/api/users/{alice.user['id']}", json={"username": "alice2"})
    assert response.json()["bio"] == "I write about Python."
    response = await alice.patch(f"/api/users/{alice.user['id']}", json={"bio": ""})
    assert response.json()["bio"] is None

    response = await alice.patch(f"/api/users/{alice.user['id']}", json={"bio": "x" * 301})
    assert response.status_code == 422
