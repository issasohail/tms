# leases/models_pcr.py
from django.db import models
from django.utils import timezone
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from PIL import Image, ImageDraw, ImageFont, ExifTags
import uuid
import io
import os


def _photo_upload_path(instance, filename):
    ext = (filename.rsplit('.', 1)[-1] or 'jpg').lower()
    return f"leases/pcr/{instance.lease_id}/photos/{uuid.uuid4()}.{ext}"


def _thumb_upload_path(instance, filename):
    return f"leases/pcr/{instance.lease_id}/photos/thumbs/{uuid.uuid4()}.jpg"


class PropertyConditionReport(models.Model):
    lease = models.OneToOneField("leases.Lease", on_delete=models.CASCADE,
                                 related_name="condition_report")
    title = models.CharField(
        max_length=120, default="Move-in Condition Photos")
    created_at = models.DateTimeField(auto_now_add=True)
    locked = models.BooleanField(default=False)

    def __str__(self):
        return f"PCR for Lease #{self.lease_id}"


class PCRPhoto(models.Model):
    pcr = models.ForeignKey(PropertyConditionReport,
                            on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=_photo_upload_path)
    thumbnail = models.ImageField(
        upload_to=_thumb_upload_path, blank=True, null=True, editable=False)
    taken_at = models.DateTimeField(blank=True, null=True)
    comment = models.CharField(max_length=300, blank=True)
    room = models.CharField(max_length=80, blank=True)
    order = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "created_at", "id"]

    @property
    def image_exists(self):
        return bool(self.image and self.image.name and default_storage.exists(self.image.name))

    @property
    def thumbnail_exists(self):
        return bool(self.thumbnail and self.thumbnail.name and default_storage.exists(self.thumbnail.name))

    @property
    def display_image_url(self):
        try:
            if self.thumbnail_exists:
                return self.thumbnail.url
            if self.image_exists:
                return self.image.url
        except Exception:
            return ""
        return ""

    @property
    def original_image_url(self):
        try:
            if self.image_exists:
                return self.image.url
        except Exception:
            return ""
        return ""

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new and not self.sort_order:
            self.sort_order = self.order or 0
        super().save(*args, **kwargs)
        if is_new and self.image:
            if not self.taken_at:
                self.taken_at = _infer_taken_at(self.image) or timezone.now()
                super().save(update_fields=["taken_at"])

            # Burn timestamp into the saved file
            try:
                stamped = _burn_datestamp(self.image, self.taken_at, self.room)
                base = os.path.basename(self.image.name)
                self.image.save(base, ContentFile(stamped), save=False)
            except Exception:
                pass

            # Make thumbnail
            try:
                thumb = _make_thumb(self.image, 512)
                self.thumbnail.save(
                    f"thumb-{uuid.uuid4()}.jpg", ContentFile(thumb), save=False)
            except Exception:
                pass

            super().save(update_fields=["image", "thumbnail"])

# ---- helpers ----


def _infer_taken_at(django_file):
    try:
        img = Image.open(django_file)
        exif = img.getexif() or {}
        tagmap = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        dt = tagmap.get("DateTimeOriginal") or tagmap.get("DateTime")
        if dt:
            from datetime import datetime
            return datetime.strptime(dt, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass


def _burn_datestamp(django_file, taken_at, room):
    img = Image.open(django_file).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    margin = int(min(w, h) * 0.02)
    text = f"{room or 'Area'} • {taken_at:%Y-%m-%d %H:%M}"
    font_size = max(18, int(h * 0.04))
    try:
        font = ImageFont.truetype("arial.ttf", size=font_size)
    except Exception:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size=font_size)
        except Exception:
            font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
    x1, y1 = w - tw - 2*margin, h - th - 2*margin
    x2, y2 = w - margin, h - margin
    draw.rounded_rectangle(
        [x1, y1, x2, y2], radius=int(th/2), fill=(20, 20, 20))
    draw.text((x2 - tw - margin, y2 - th - margin),
              text, fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90, optimize=True)
    out.seek(0)
    return out.read()


def _make_thumb(django_file, max_size=512):
    img = Image.open(django_file).convert("RGB")
    img.thumbnail((max_size, max_size))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
    out.seek(0)
    return out.read()
