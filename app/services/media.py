"""Question images. Files are re-encoded with Pillow (drops metadata, rejects non-images) and served
from /media/questions/<random>.png|jpg - random names, so <img src> works without a token."""
import io
import secrets
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.errors import bad_request

MAX_BYTES = 3 * 1024 * 1024
MAX_SIDE = 1600


def media_dir() -> Path:
    p = Path(settings.STORAGE_DIR) / "media" / "questions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_question_image(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise bad_request("IMAGE_TOO_LARGE", "Image must be 3 MB or smaller")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise bad_request("BAD_IMAGE", "Upload a PNG, JPG or WEBP image")
    if img.format not in ("PNG", "JPEG", "WEBP", "GIF", "BMP"):
        raise bad_request("BAD_IMAGE", "Upload a PNG, JPG or WEBP image")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    has_alpha = img.mode in ("RGBA", "LA", "P")
    name = secrets.token_hex(12) + (".png" if has_alpha or img.format == "PNG" else ".jpg")
    out = media_dir() / name
    if name.endswith(".png"):
        img.save(out, "PNG", optimize=True)
    else:
        img.convert("RGB").save(out, "JPEG", quality=88)
    return name


def delete_question_image(name: str | None):
    if name:
        p = media_dir() / Path(name).name
        if p.exists():
            p.unlink()


def image_url(name: str | None) -> str | None:
    return f"/media/questions/{name}" if name else None


def image_path(name: str | None) -> Path | None:
    if not name:
        return None
    p = media_dir() / Path(name).name
    return p if p.exists() else None


def avatar_dir() -> Path:
    p = Path(settings.STORAGE_DIR) / "media" / "avatars"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save_avatar(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise bad_request("IMAGE_TOO_LARGE", "Image must be 3 MB or smaller")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError):
        raise bad_request("BAD_IMAGE", "Upload a PNG, JPG or WEBP image")
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img).convert("RGB")
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2, (img.width + side) // 2, (img.height + side) // 2))
    img = img.resize((400, 400))
    name = secrets.token_hex(12) + ".jpg"
    img.save(avatar_dir() / name, "JPEG", quality=88)
    return name


def delete_avatar(name: str | None):
    if name:
        p = avatar_dir() / Path(name).name
        if p.exists():
            p.unlink()
