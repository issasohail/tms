# leases/models_lease_photos.py
from __future__ import annotations
import re as _re_safe
import os
import re
import uuid
import time
import tempfile
from io import BytesIO
from datetime import datetime

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from django.dispatch import receiver
from django.db.models.signals import post_save
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ExifTags
from zoneinfo import ZoneInfo

try:
    from core.models import GlobalSettings
except Exception:
    GlobalSettings = None
from PIL import Image, ImageDraw, ImageFont, ExifTags, ImageOps

from PIL import Image, ImageDraw, ImageFont, ExifTags, ImageOps

# Hard technical limit in Pillow is ~65500 on any side.
PIL_SIDE_LIMIT = 65500

# Your safety caps (tunable via settings if you like)
MAX_STAMP_SIDE   = getattr(settings, "LEASE_MAX_STAMP_SIDE", 12000)       # max width/height before stamping
MAX_STAMP_PIXELS = getattr(settings, "LEASE_MAX_STAMP_PIXELS", 80_000_000)  # 80 MP guard
MAX_THUMB_SIDE   = getattr(settings, "LEASE_MAX_THUMB_SIDE", 512)

def _prepare_base_for_stamp(raw_bytes: bytes) -> Image.Image:
    """
    Load bytes, apply EXIF orientation, and downscale if an image is too large
    so that any subsequent (w, h + footer) result won't approach PIL limits.
    """
    img = Image.open(BytesIO(raw_bytes))
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size

    # Absolute safety cap: never let either side be near PIL limit
    hard_cap = min(MAX_STAMP_SIDE, PIL_SIDE_LIMIT - 1000)  # leave headroom for footer
    needs_downscale = (
        max(w, h) > hard_cap or
        (w * h) > MAX_STAMP_PIXELS
    )
    if needs_downscale:
        img.thumbnail((hard_cap, hard_cap), Image.LANCZOS)
    return img

# -------------------------------------------------------------------
# config
# -------------------------------------------------------------------
MAX_DB_PATH = 115
STAMP_PROP_SCALE = getattr(settings, "LEASE_STAMP_PROP_SCALE",   0.55)
STAMP_TS_SCALE = getattr(settings, "LEASE_STAMP_TS_SCALE",     0.50)
STAMP_DESC_SCALE = getattr(settings, "LEASE_STAMP_DESC_SCALE",   0.50)
STAMP_SCALE = getattr(settings, "LEASE_STAMP_SCALE",        1.0)
# Resolve a default font path if LEASE_STAMP_FONT isn't set.
# 1) If settings.BASE_DIR exists, use it.
# 2) Otherwise, walk up from this file (…/leases/models_lease_photos.py) to project root.
_base_dir = getattr(settings, "BASE_DIR", None)
if _base_dir is None:
    _base_dir = Path(__file__).resolve().parents[2]  # adjust if your project depth differs

_default_font = Path(_base_dir) / "core" / "static" / "fonts" / "Inter-Regular.ttf"
STAMP_FONT = getattr(settings, "LEASE_STAMP_FONT", str(_default_font))

STAMP_MIN_PX = getattr(settings, "LEASE_STAMP_MIN_PX",       12)

# -------------------------------------------------------------------
# naming helpers
# -------------------------------------------------------------------


def _fs_part(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r'[<>:"/\\|#?*]+', "-", s)
    return s.rstrip(". ")


def _folder_name_for_lease(lease) -> str:
    unit = _fs_part(getattr(getattr(lease, "unit", None), "unit_number", "")
                    or getattr(lease, "unit_number", "") or "unit")
    tenant = getattr(lease, "tenant", None)
    tenant_name = getattr(tenant, "full_name", str(tenant)) or ""
    tenant10 = _fs_part(tenant_name)[:10] or "tenant"
    end = getattr(lease, "end_date", None)
    end_str = end.strftime("%Y-%m-%d") if end else "unknown"
    return f"{unit}-{tenant10}-{end_str}"


def _base_dir(i: "LeaseMedia") -> str:
    return f"leases/lease_photos/{_folder_name_for_lease(i.lease)}"


def _photo_filename(i: "LeaseMedia", ext: str) -> str:
    if not ext.startswith("."):
        ext = "." + ext
    return f"{_folder_name_for_lease(i.lease)}-{i.pk or 'new'}{ext.lower()}"


def _cap_path(no_ext: str, ext: str) -> str:
    if not ext.startswith("."):
        ext = "." + ext
    path = no_ext + ext
    if len(path) <= MAX_DB_PATH:
        return path
    folder, name = os.path.split(no_ext)
    while len(f"{folder}/{name}{ext}") > MAX_DB_PATH and len(name) > 20:
        name = name[:-1]
    return f"{folder}/{name}{ext}"


def _photos_path(i: "LeaseMedia", ext: str) -> str:
    base = _base_dir(i)
    no_ext = f"{base}/photos/{_photo_filename(i, '').rstrip('.')}"
    return _cap_path(no_ext, ext or ".jpg")


def _photos_path_versioned(i: "LeaseMedia", ext: str) -> str:
    token = uuid.uuid4().hex[:6].upper()
    base = _base_dir(i)
    name = f"{_folder_name_for_lease(i.lease)}-{i.pk or 'new'}-r{token}"
    no_ext = f"{base}/photos/{name}"
    return _cap_path(no_ext, ext or ".jpg")


def _thumbs_path(i: "LeaseMedia") -> str:
    base = _base_dir(i)
    no_ext = f"{base}/thumbs/{_photo_filename(i, '').rstrip('.')}"
    return _cap_path(no_ext, ".jpg")


def _thumbs_path_versioned(i: "LeaseMedia") -> str:
    token = uuid.uuid4().hex[:6].upper()
    base = _base_dir(i)
    name = f"{_folder_name_for_lease(i.lease)}-{i.pk or 'new'}-r{token}"
    no_ext = f"{base}/thumbs/{name}"
    return _cap_path(no_ext, ".jpg")


def _upload_tmp(i: "LeaseMedia", filename: str) -> str:
    ext = (os.path.splitext(filename)[1] or ".bin").lower()
    return f"lm_tmp/{i.lease_id}/{uuid.uuid4().hex}{ext}"

# -------------------------------------------------------------------
# filesystem helpers (atomic write)
# -------------------------------------------------------------------


def _ensure_parent_dir(path: str) -> None:
    try:
        if hasattr(default_storage, "path"):
            abs_dir = os.path.dirname(default_storage.path(path))
            os.makedirs(abs_dir, exist_ok=True)
    except Exception:
        pass


def _write_bytes_exact(path: str, data: bytes, retries: int = 5, sleep_s: float = 0.25) -> None:
    _ensure_parent_dir(path)
    tmp = f"{path}.tmp-{uuid.uuid4().hex}"
    default_storage.save(tmp, ContentFile(data))

    if hasattr(default_storage, "path"):
        tmp_abs = default_storage.path(tmp)
        dst_abs = default_storage.path(path)
        for i in range(retries):
            try:
                os.replace(tmp_abs, dst_abs)
                break
            except PermissionError:
                if i == retries - 1:
                    raise
                time.sleep(sleep_s)
        try:
            if os.path.exists(tmp_abs):
                os.remove(tmp_abs)
        except Exception:
            pass
    else:
        with default_storage.open(tmp, "rb") as fh:
            default_storage.save(path, ContentFile(fh.read()))
        try:
            default_storage.delete(tmp)
        except Exception:
            pass


def _listdir(dir_rel: str) -> list[str]:
    if not default_storage.exists(dir_rel):
        return []
    _, files = default_storage.listdir(dir_rel)
    return files


def _cleanup_versioned_siblings(i: "LeaseMedia", kind: str) -> None:
    base_dir = _base_dir(i)
    folder = _folder_name_for_lease(i.lease)
    pid = i.pk or "new"
    dir_name = "photos" if kind == "photo" else "thumbs"
    dir_rel = f"{base_dir}/{dir_name}"
    if not default_storage.exists(dir_rel):
        return
    pat = re.compile(
        rf"^{re.escape(folder)}-{re.escape(str(pid))}-r[A-F0-9]{{6}}\.jpg$", re.IGNORECASE)
    for fn in _listdir(dir_rel):
        if pat.match(fn):
            try:
                default_storage.delete(f"{dir_rel}/{fn}")
            except Exception:
                pass

def _try_replace_canonical(i: "LeaseMedia", kind: str, data: bytes,
                           retries: int = 10, sleep_s: float = 0.3) -> str:
    if kind == "photo":
        canonical = _photos_path(i, ".jpg")
        versioned = _photos_path_versioned(i, ".jpg")
    else:
        canonical = _thumbs_path(i)
        versioned = _thumbs_path_versioned(i)

    # Local FS: atomic write/rename
    if hasattr(default_storage, "path"):
        try:
            _write_bytes_exact(canonical, data)
            _cleanup_versioned_siblings(i, kind)
            return canonical
        except PermissionError:
            pass
        _write_bytes_exact(versioned, data)
        can_abs = default_storage.path(canonical)
        ver_abs = default_storage.path(versioned)
        os.makedirs(os.path.dirname(can_abs), exist_ok=True)
        for _ in range(retries):
            try:
                try:
                    if os.path.exists(can_abs):
                        os.remove(can_abs)
                except Exception:
                    pass
                os.replace(ver_abs, can_abs)
                _cleanup_versioned_siblings(i, kind)
                return canonical
            except PermissionError:
                time.sleep(sleep_s)
            except FileNotFoundError:
                _write_bytes_exact(versioned, data)
                ver_abs = default_storage.path(versioned)
        return versioned

    # Remote FS (e.g., S3): overwrite canonical key, then cleanup
    try:
        if default_storage.exists(canonical):
            default_storage.delete(canonical)
    except Exception:
        pass
    default_storage.save(canonical, ContentFile(data))
    _cleanup_versioned_siblings(i, kind)
    return canonical



# -------------------------------------------------------------------
# model
# -------------------------------------------------------------------

from PIL import ImageOps
class LeaseMedia(models.Model):
    lease = models.ForeignKey(
        "leases.Lease", on_delete=models.CASCADE, related_name="media")

    file = models.FileField(
        max_length=255,
        upload_to=_upload_tmp,
        validators=[FileExtensionValidator(
            ["jpg", "jpeg", "png", "gif", "mp4", "mov", "avi", "mkv"])],
    )
    # single-file strategy: no originals field
    thumbnail = models.ImageField(
        max_length=255,
        upload_to=_upload_tmp, blank=True, null=True, editable=False)

    media_type = models.CharField(max_length=10, choices=[
                                  ("image", "Image"), ("video", "Video")])
    title = models.CharField(max_length=120, blank=True)
    description = models.CharField(max_length=300, blank=True)
    taken_at = models.DateTimeField(blank=True, null=True)

    # immutable + layout metadata for footer rebuilds
    static_footer_text = models.CharField(
        max_length=200, blank=True, editable=False)
    footer_height_px = models.PositiveIntegerField(default=0, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def build_friendly_filename(self, force_ext: str | None = None) -> str:
        base = f"{_folder_name_for_lease(self.lease)}-{self.pk or 'new'}"
        ext = (force_ext or os.path.splitext(self.file.name)[1] or ".jpg")
        if not ext.startswith("."):
            ext = "." + ext
        return base + ext

    def delete(self, *args, **kwargs):
        def _move(src_path: str, suffix: str):
            if not src_path or not default_storage.exists(src_path):
                return
            base_dir = f"{_base_dir(self)}/deleted_photos"
            base = os.path.splitext(os.path.basename(src_path))[0]
            ext = os.path.splitext(os.path.basename(src_path))[1] or ".jpg"
            dst = f"{base_dir}/{base}{suffix}{ext}"

            if hasattr(default_storage, "path"):
                try:
                    os.makedirs(os.path.dirname(
                        default_storage.path(dst)), exist_ok=True)
                except Exception:
                    pass
                src_abs = default_storage.path(src_path)
                dst_abs = default_storage.path(dst)
                for i in range(5):
                    try:
                        os.replace(src_abs, dst_abs)
                        return
                    except PermissionError:
                        if i == 4:
                            with default_storage.open(src_path, "rb") as fh:
                                _write_bytes_exact(dst, fh.read())
                            try:
                                default_storage.delete(src_path)
                            except Exception:
                                pass
                            return
                        time.sleep(0.25)
            else:
                with default_storage.open(src_path, "rb") as fh:
                    _write_bytes_exact(dst, fh.read())
                try:
                    default_storage.delete(src_path)
                except Exception:
                    pass

        _move(getattr(self.file, "name", None), "-photo")
        _move(getattr(self.thumbnail, "name", None), "-thumb")
        super().delete(*args, **kwargs)

    # ----------------- core pipeline -----------------
    @staticmethod
    def _is_tmp_path(name: str) -> bool:
        n = (name or "").replace("\\", "/").lower()
        return ("lm_tmp/" in n) or ("/_tmp/" in n) or n.startswith("lm_tmp/")

    def _compose_static_footer_text(self) -> str:
        # Prefer already-present fields; avoid extra queries
        prop = ""
        unit_num = ""

        try:
            # If your Lease model has denormalized fields, use them first
            prop = getattr(self.lease, "property_name", "") or prop
            unit_num = getattr(self.lease, "unit_number", "") or unit_num
        except Exception:
            pass

        # Try related objects, but never crash if the DB connection hiccups
        if not prop or not unit_num:
            try:
                unit = getattr(self.lease, "unit", None)
                if unit is not None:
                    unit_num = unit_num or getattr(unit, "unit_number", "") or ""
                    pr = getattr(unit, "property", None)
                    if pr is not None:
                        prop = prop or getattr(pr, "property_name", "") or ""
            except Exception:
                # Fallbacks — don't let a DB blip kill the request
                pass

        prop_unit = f"{(prop or '')[:10]}-{unit_num or ''}".strip("-")

        tz = _effective_timezone()
        local_ts = timezone.localtime(self.taken_at, tz) if getattr(self, "taken_at", None) else None
        ts_text = f"Taken: {local_ts:%Y-%m-%d %H:%M}" if local_ts else "Taken: —"

        return f"{prop_unit} • {ts_text}"


    def _compose_footer(self, base_img: Image.Image, *, static_line: str, desc: str) -> tuple[bytes, int]:
        """Return (stamped_jpeg_bytes, footer_height_px)."""
        w, h = base_img.size
        base = int(max(50, h * 0.08) * float(STAMP_SCALE))
        sz_static = max(STAMP_MIN_PX, int(base * STAMP_TS_SCALE))
        sz_desc = max(STAMP_MIN_PX, int(base * STAMP_DESC_SCALE))

        def load_font(sz):
            try:
                return ImageFont.truetype(STAMP_FONT, size=sz)
            except Exception:
                return ImageFont.load_default()

        font_static = load_font(sz_static)
        font_desc = load_font(sz_desc)

        tmp = Image.new("RGB", (10, 10), "white")
        d = ImageDraw.Draw(tmp)

        def text_w(txt, font):
            if not txt:
                return 0
            return d.textlength(txt, font=font) if hasattr(d, "textlength") else d.textbbox((0, 0), txt, font=font)[2]

        def text_h(txt, font):
            if not txt:
                return 0
            b = d.textbbox((0, 0), txt, font=font)
            return b[3]-b[1]

        margin_x = 12
        usable_w = w - 2*margin_x

        def shrink_to_fit(txt, font, min_px, current_size):
            size = getattr(font, "size", current_size)
            f = font
            while text_w(txt, f) > usable_w and size > min_px:
                size -= 1
                try:
                    f = ImageFont.truetype(STAMP_FONT, size=size)
                except Exception:
                    f = ImageFont.load_default()
            return f

        static_line = static_line or ""
        font_static = shrink_to_fit(
            static_line, font_static, STAMP_MIN_PX, sz_static)
        static_h = text_h(static_line, font_static)

        def wrap_text(txt, font, max_width, max_lines=3):
            txt = (txt or "").strip()
            if not txt:
                return []
            words, lines, cur = txt.split(), [], ""
            for w_ in words:
                test = w_ if not cur else cur + " " + w_
                if text_w(test, font) <= max_width:
                    cur = test
                else:
                    if cur:
                        lines.append(cur)
                    cur = w_
                    if len(lines) == max_lines - 1:
                        break
            if cur:
                lines.append(cur)
            if lines and text_w(lines[-1], font) > max_width:
                s = lines[-1]
                while s and text_w(s + "…", font) > max_width:
                    s = s[:-1]
                lines[-1] = (s + "…") if s else ""
            return lines

        desc_lines = wrap_text(
            self.description if desc is None else desc, font_desc, usable_w, max_lines=3)
        desc_line_h = text_h("Ag", font_desc) if desc_lines else 0

        gap = max(4, int(base * 0.20))
        pad_top = 6
        pad_bottom = max(10, int(base * 0.35))
        desc_block_h = 0 if not desc_lines else (
            len(desc_lines)*desc_line_h + (len(desc_lines)-1)*gap)
        footer_h = static_h + (gap if desc_lines else 0) + \
            desc_block_h + pad_top + pad_bottom

        new_img = Image.new("RGB", (w, h + footer_h), "white")
        new_img.paste(base_img, (0, 0))
        draw = ImageDraw.Draw(new_img)
        y = h + pad_top
        if static_line:
            draw.text((12, y), static_line, fill="black", font=font_static)
            y += static_h
        if desc_lines:
            y += gap
            for i, ln in enumerate(desc_lines):
                draw.text((12, y), ln, fill="black", font=font_desc)
                y += desc_line_h
                if i < len(desc_lines) - 1:
                    y += gap

        out = BytesIO()
        new_img.save(out, format="JPEG", quality=92,
                     optimize=True, progressive=True)
        out.seek(0)
        try:
            new_img.close()
        except Exception:
            pass
        return out.read(), int(footer_h)

    def generate_thumbnail(self, stamped_bytes: bytes | None = None):
        if stamped_bytes is None:
            with default_storage.open(self.file.name, "rb") as f:
                stamped_bytes = f.read()
        img = ImageOps.exif_transpose(Image.open(BytesIO(stamped_bytes))).convert("RGB")
        img.thumbnail((MAX_THUMB_SIDE, MAX_THUMB_SIDE), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True, progressive=True)
        buf.seek(0)
        final_thumb_rel = _try_replace_canonical(self, "thumb", buf.getvalue())
        self.thumbnail.name = final_thumb_rel
        super().save(update_fields=["thumbnail", "updated_at"])



    def reburn_from_stamped(self):
        if self.media_type != "image" or not self.file:
            return
        if not self.footer_height_px or not self.static_footer_text:
            return

        with default_storage.open(self.file.name, "rb") as fh:
            stamped_img = ImageOps.exif_transpose(Image.open(fh)).convert("RGB")
            w, h = stamped_img.size
            base_h = max(1, h - int(self.footer_height_px))
            base = stamped_img.crop((0, 0, w, base_h))

        # Safety: if the base is too large, downscale it before adding a footer
        bw, bh = base.size
        hard_cap = min(MAX_STAMP_SIDE, PIL_SIDE_LIMIT - 1000)
        if max(bw, bh) > hard_cap or (bw * bh) > MAX_STAMP_PIXELS:
            base.thumbnail((hard_cap, hard_cap), Image.LANCZOS)

        stamped_bytes, new_footer_h = self._compose_footer(
            base, static_line=self.static_footer_text, desc=self.description or ""
        )

        final_photo_rel = _try_replace_canonical(self, "photo", stamped_bytes)
        self.file.name = final_photo_rel
        self.footer_height_px = int(new_footer_h)
        super().save(update_fields=["file", "footer_height_px", "updated_at"])
        self.generate_thumbnail(stamped_bytes)


    def save(self, *args, **kwargs):
        """
        First/any save where file is in tmp or no thumbnail:
          - detect media type & taken_at
          - if image: stamp from upload, write to /photos, make /thumbs, delete tmp
          - if video: move to /videos
        Later saves: views call reburn_from_stamped() when description changes.
        """
        was_adding = self._state.adding  # capture before first save
        super().save(*args, **kwargs)    # ensure pk and write tmp

        if not self.file:
            return

        file_name = (self.file.name or "")
        ext = os.path.splitext(file_name)[1].lower() or ".jpg"
        is_video = ext in [".mp4", ".mov", ".avi", ".mkv"]
        self.media_type = "video" if is_video else "image"

        in_tmp = self._is_tmp_path(file_name)
        need_process = was_adding or in_tmp or (
            self.media_type == "image" and not self.thumbnail)

        if not need_process:
            return

        # taken_at
        if not self.taken_at:
            if self.media_type == "image":
                self.taken_at = _infer_taken_at(self.file)
            if not self.taken_at:
                self.taken_at = timezone.now()

        if self.media_type == "image":
            # read uploaded bytes (tmp)
            with default_storage.open(file_name, "rb") as fh:
                raw = fh.read()
            base_img = _prepare_base_for_stamp(raw)

            
            base_img = ImageOps.exif_transpose(Image.open(BytesIO(raw))).convert("RGB")


            # immutable footer & compose stamped bytes
            static_line = self._compose_static_footer_text()
            stamped_bytes, footer_h = self._compose_footer(
                base_img, static_line=static_line, desc=self.description or "")

            # write stamped to /photos
            final_photo_rel = _try_replace_canonical(
                self, "photo", stamped_bytes)
            self.file.name = final_photo_rel
            self.static_footer_text = static_line
            self.footer_height_px = int(footer_h)

            # write thumb
            self.generate_thumbnail(stamped_bytes)

            # delete tmp upload (best-effort)
            if in_tmp and default_storage.exists(file_name):
                try:
                    default_storage.delete(file_name)
                except Exception:
                    pass

            super().save(update_fields=["file", "media_type", "taken_at",
                                        "static_footer_text", "footer_height_px", "updated_at"])

        else:
            # VIDEO: move into /videos
            video_dir = f"{_base_dir(self)}/videos"
            video_name = _photo_filename(self, ext)
            path_no_ext = f"{video_dir}/{os.path.splitext(video_name)[0]}"
            video_path = _cap_path(path_no_ext, ext)
            with default_storage.open(file_name, "rb") as fh:
                data = fh.read()
            _write_bytes_exact(video_path, data)
            self.file.name = video_path

            if in_tmp and default_storage.exists(file_name):
                try:
                    default_storage.delete(file_name)
                except Exception:
                    pass

            super().save(update_fields=[
                "file", "media_type", "taken_at", "updated_at"])

# -------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------

from django.utils import timezone as dj_tz

def _infer_taken_at(django_file) -> datetime | None:
    try:
        im = Image.open(django_file)
        exif = im.getexif() or {}
        tagmap = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        dt = tagmap.get("DateTimeOriginal") or tagmap.get("DateTime")
        if dt:
            naive = datetime.strptime(dt, "%Y:%m:%d %H:%M:%S")
            # make aware in the effective timezone, then convert to settings.TIME_ZONE
            aware = dj_tz.make_aware(naive, _effective_timezone())
            return aware.astimezone(dj_tz.get_current_timezone())
    except Exception:
        pass
    return None



def _effective_timezone() -> ZoneInfo:
    tzname = None
    if GlobalSettings is not None:
        try:
            tzname = GlobalSettings.get_solo().time_zone
        except Exception:
            tzname = None
    tzname = tzname or getattr(settings, "TIME_ZONE", "UTC")
    try:
        return ZoneInfo(tzname)
    except Exception:
        return ZoneInfo("UTC")

# -------------------------------------------------------------------
# safety net + auto-PDF
# -------------------------------------------------------------------


@receiver(post_save, sender=LeaseMedia)
def _finalize_tmp_to_final(sender, instance: "LeaseMedia", created, **kwargs):
    """
    If a row still references tmp and has no thumbnail yet, re-run processing once.
    (Prevents infinite loops and needless work.)
    """
    name = (getattr(instance.file, "name", "") or "")
    if not LeaseMedia._is_tmp_path(name):
        return
    if instance.thumbnail:  # already processed
        return
    try:
        instance.save()
    except Exception:
        pass

from django.conf import settings as dj_settings
@receiver(post_save, sender=LeaseMedia)
def _auto_export_pdf(sender, instance, **kwargs):

    if not getattr(dj_settings, "LEASE_AUTO_PDF_ON_SAVE", False):
        return  # <-- no PDF on each save by default

    try:
        from .services.export_lease_photos_pdf import export_lease_photos_pdf
    except Exception:
        return


    lease = instance.lease
    res = export_lease_photos_pdf(lease)
    if not res:
        return
    name, fileobj = res
    if not fileobj:
        return

    folder = _folder_name_for_lease(lease)
    base_dir = f"leases/lease_photos/{folder}"
    pdf_path = f"{base_dir}/{folder}.pdf"

    try:
        pdf_bytes = fileobj.read()
        _write_bytes_exact(pdf_path, pdf_bytes)
        if default_storage.exists(base_dir):
            _, filenames = default_storage.listdir(base_dir)
            for fn in filenames:
                if fn.lower().endswith(".pdf") and fn != f"{folder}.pdf":
                    try:
                        default_storage.delete(f"{base_dir}/{fn}")
                    except Exception:
                        pass
    except Exception:
        pass


def safe_name(s: str) -> str:
    """
    Make a short, filesystem-safe name (keep letters, numbers, dot, underscore, dash).
    Collapses whitespace and replaces other chars with '-'.
    """
    return _re_safe.sub(r"[^A-Za-z0-9._-]+", "-", (s or "").strip())
# --- end helper -
