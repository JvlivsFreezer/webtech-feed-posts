"""Regression checks for the runnable feature extraction."""

from conftest import create_post


async def test_following_and_bookmarks_pages_filter_posts(make_user):
    alice = await make_user("alice")
    bob = await make_user("bob")
    carol = await make_user("carol")
    bobs_post = await create_post(bob, title="Followed writer entry")
    carols_post = await create_post(carol, title="Saved writer entry")
    await alice.post(f"/api/users/{bob.user['id']}/follow")
    await alice.post(f"/api/posts/{carols_post['id']}/bookmark")

    # Check card fragments: the sidebar intentionally lists other community posts.
    response = await alice.get("/partials/posts?source=feed")
    assert response.status_code == 200
    assert bobs_post["title"] in response.text
    assert carols_post["title"] not in response.text
    response = await alice.get("/partials/posts?source=bookmarks")
    assert response.status_code == 200
    assert carols_post["title"] in response.text
    assert bobs_post["title"] not in response.text
    for path in ("/feed", "/bookmarks"):
        assert (await alice.get(path)).status_code == 200


async def test_account_management_is_excluded(make_user):
    alice = await make_user("alice")
    assert (await alice.get("/account")).status_code == 404
    assert (await alice.patch(f"/api/users/{alice.user['id']}", json={"bio": "Edited"})).status_code == 405
    assert (await alice.delete(f"/api/users/{alice.user['id']}")).status_code == 405
    for path in ("/", f"/users/{alice.user['id']}/posts", "/verify-email"):
        response = await alice.get(path)
        assert response.status_code == 200
        assert 'href="http://testserver/account"' not in response.text


async def test_verification_can_be_resent_without_account_settings(make_user, sent_emails):
    alice = await make_user("alice")
    response = await alice.get("/verify-email")
    assert 'id="resendVerification"' in response.text
    sent_emails.clear()
    assert (await alice.post("/api/users/me/resend-verification")).status_code == 202
    assert len(sent_emails) == 1
    token = sent_emails[0]["plain_text"].split("token=")[1].split()[0]
    response = await alice.post("/api/users/verify-email", json={"token": token})
    assert response.status_code == 200
    assert (await alice.get("/api/users/me")).json()["email_verified"] is True
