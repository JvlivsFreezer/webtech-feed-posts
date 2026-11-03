import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

PROFILE_PICS_DIR = Path("media/profile_pics")
POST_IMAGES_DIR = Path("media/post_images")

POST_IMAGE_MAX_SIZE = (1200, 1200)


def _save_as_jpeg(img: Image.Image, directory: Path) -> str:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")

    filename = f"{uuid.uuid4().hex}.jpg"
    directory.mkdir(parents=True, exist_ok=True)
    img.save(directory / filename, "JPEG", quality=85, optimize=True)
    return filename


def _delete_image(directory: Path, filename: str | None) -> None:
    if filename is None:
        return

    filepath = directory / filename
    if filepath.exists():
        filepath.unlink()


def process_profile_image(content: bytes) -> str:
    with Image.open(BytesIO(content)) as original:
        img = ImageOps.exif_transpose(original)
        img = ImageOps.fit(img, (300, 300), method=Image.Resampling.LANCZOS)
        return _save_as_jpeg(img, PROFILE_PICS_DIR)


def delete_profile_image(filename: str | None) -> None:
    _delete_image(PROFILE_PICS_DIR, filename)


def process_post_image(content: bytes) -> str:
    """Shrink to fit within POST_IMAGE_MAX_SIZE, keeping the aspect ratio."""
    with Image.open(BytesIO(content)) as original:
        img = ImageOps.exif_transpose(original)
        img.thumbnail(POST_IMAGE_MAX_SIZE, Image.Resampling.LANCZOS)
        return _save_as_jpeg(img, POST_IMAGES_DIR)


def delete_post_image(filename: str | None) -> None:
    _delete_image(POST_IMAGES_DIR, filename)
