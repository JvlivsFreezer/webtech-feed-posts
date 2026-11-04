"""Email verification and image uploads."""

from io import BytesIO

from PIL import Image

from conftest import PASSWORD, create_post


def verification_token(email: dict) -> str:
    return email["plain_text"].split("token=")[1].split()[0]


def png_bytes(size=(1600, 900)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, "steelblue").save(buffer, "PNG")
    return buffer.getvalue()


async def test_registration_sends_verification_email(client, sent_emails):
    response = await client.post(
        "/api/users",
        json={"username": "alice", "email": "alice@example.com", "password": PASSWORD},
    )
    assert response.status_code == 201
    assert len(sent_emails) == 1
    assert sent_emails[0]["to_email"] == "alice@example.com"
    assert "/verify-email?token=" in sent_emails[0]["plain_text"]

    token = verification_token(sent_emails[0])
    response = await client.post("/api/users/verify-email", json={"token": token})
    assert response.status_code == 200

    # Tokens are single-use
    response = await client.post("/api/users/verify-email", json={"token": token})
    assert response.status_code == 400


async def test_verified_flag_shows_on_me(make_user, client, sent_emails):
    alice = await make_user("alice")
    assert (await alice.get("/api/users/me")).json()["email_verified"] is False

    await client.post(
        "/api/users/verify-email",
        json={"token": verification_token(sent_emails[-1])},
    )
    assert (await alice.get("/api/users/me")).json()["email_verified"] is True

    response = await alice.post("/api/users/me/resend-verification")
    assert response.status_code == 400


async def test_invalid_verification_token(client):
    response = await client.post("/api/users/verify-email", json={"token": "bogus"})
    assert response.status_code == 400


async def test_resend_replaces_old_token(make_user, client, sent_emails):
    alice = await make_user("alice")
    old_token = verification_token(sent_emails[-1])

    assert (await alice.post("/api/users/me/resend-verification")).status_code == 202
    new_token = verification_token(sent_emails[-1])
    assert new_token != old_token

    assert (await client.post("/api/users/verify-email", json={"token": old_token})).status_code == 400
    assert (await client.post("/api/users/verify-email", json={"token": new_token})).status_code == 200


async def test_changing_email_requires_reverification(make_user, client, sent_emails):
    alice = await make_user("alice")
    await client.post(
        "/api/users/verify-email",
        json={"token": verification_token(sent_emails[-1])},
    )

    response = await alice.patch(
        f"/api/users/{alice.user['id']}",
        json={"email": "new@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["email_verified"] is False
    assert sent_emails[-1]["to_email"] == "new@example.com"


async def test_post_image_upload_and_delete(make_user, media_dirs):
    alice = await make_user("alice")
    post = await create_post(alice)

    response = await alice.patch(
        f"/api/posts/{post['id']}/image",
        files={"file": ("photo.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    image_path = response.json()["image_path"]
    assert image_path.startswith("/media/post_images/")

    saved = media_dirs / "post_images" / image_path.rsplit("/", 1)[1]
    assert saved.exists()
    with Image.open(saved) as img:
        # Shrunk to fit 1200px, aspect ratio kept
        assert img.size == (1200, 675)

    response = await alice.delete(f"/api/posts/{post['id']}/image")
    assert response.status_code == 200
    assert response.json()["image_path"] is None
    assert not saved.exists()


async def test_post_image_rejects_non_images_and_other_users(make_user):
    alice = await make_user("alice")
    bob = await make_user("bob")
    post = await create_post(alice)

    response = await alice.patch(
        f"/api/posts/{post['id']}/image",
        files={"file": ("notes.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 400

    response = await bob.patch(
        f"/api/posts/{post['id']}/image",
        files={"file": ("photo.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 403


async def test_deleting_post_removes_its_image(make_user, media_dirs):
    alice = await make_user("alice")
    post = await create_post(alice)
    await alice.patch(
        f"/api/posts/{post['id']}/image",
        files={"file": ("photo.png", png_bytes((100, 100)), "image/png")},
    )
    assert len(list((media_dirs / "post_images").iterdir())) == 1

    await alice.delete(f"/api/posts/{post['id']}")
    assert list((media_dirs / "post_images").iterdir()) == []


async def test_profile_picture_upload(make_user, media_dirs):
    alice = await make_user("alice")
    response = await alice.patch(
        f"/api/users/{alice.user['id']}/picture",
        files={"file": ("me.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["image_path"].startswith("/media/profile_pics/")
    assert len(list((media_dirs / "profile_pics").iterdir())) == 1
