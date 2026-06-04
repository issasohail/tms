import os
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
except Exception:
    register_heif_opener = None

if register_heif_opener:
    register_heif_opener()


IMAGE_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


def is_image_upload_name(filename):
    return os.path.splitext(filename or "")[1].lower() in IMAGE_UPLOAD_EXTENSIONS


def compress_uploaded_image(upload, max_side=1600, quality=82):
    if not upload or not is_image_upload_name(getattr(upload, "name", "")):
        return upload

    try:
        upload.seek(0)
    except Exception:
        pass

    try:
        with Image.open(upload) as image:
            image = ImageOps.exif_transpose(image)
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )

            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.LANCZOS)

            original_name = os.path.basename(getattr(upload, "name", "") or "upload")
            stem, ext = os.path.splitext(original_name)
            ext = ext.lower()
            out = BytesIO()

            if has_alpha and ext == ".png":
                image.save(out, format="PNG", optimize=True)
                new_ext = ".png"
                content_type = "image/png"
            else:
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.save(
                    out,
                    format="JPEG",
                    quality=quality,
                    optimize=True,
                    progressive=True,
                )
                new_ext = ".jpg"
                content_type = "image/jpeg"

            compressed = ContentFile(out.getvalue())
            compressed.name = f"{stem or 'upload'}{new_ext}"
            compressed.content_type = content_type
            return compressed
    except Exception:
        try:
            upload.seek(0)
        except Exception:
            pass
        return upload


def compress_instance_file_field(instance, field_name, max_side=1600, quality=82):
    field_file = getattr(instance, field_name, None)
    if not field_file or not getattr(field_file, "name", ""):
        return
    if getattr(field_file, "_committed", True):
        return
    compressed = compress_uploaded_image(field_file, max_side=max_side, quality=quality)
    if compressed is not field_file:
        field_file.save(compressed.name, compressed, save=False)
