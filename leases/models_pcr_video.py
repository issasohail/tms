from django.db import models
from django.utils import timezone
from django.core.files.base import ContentFile
import uuid
import os
import hashlib
import json
import tempfile
import pathlib
import subprocess


def _video_upload_path(instance, filename):
    ext = (filename.rsplit(".", 1)[-1] or "mp4").lower()
    return f"leases/pcr/{instance.pcr.lease_id}/videos/{uuid.uuid4()}.{ext}"


def _poster_upload_path(instance, filename):
    return f"leases/pcr/{instance.pcr.lease_id}/videos/posters/{uuid.uuid4()}.jpg"


class PCRVideo(models.Model):
    pcr = models.ForeignKey("leases.PropertyConditionReport",
                            on_delete=models.CASCADE, related_name="videos")
    file = models.FileField(upload_to=_video_upload_path)
    poster = models.ImageField(
        upload_to=_poster_upload_path, blank=True, null=True, editable=False)
    encoded_mp4 = models.FileField(
        upload_to=_video_upload_path, blank=True, null=True, editable=False)
    # Minimal fields
    comment = models.CharField(max_length=300, blank=True)
    room = models.CharField(max_length=80, blank=True)
    order = models.PositiveIntegerField(default=0)

    taken_at = models.DateTimeField(blank=True, null=True)
    duration_seconds = models.FloatField(blank=True, null=True)
    width = models.PositiveIntegerField(blank=True, null=True)
    height = models.PositiveIntegerField(blank=True, null=True)
    sha256 = models.CharField(max_length=64, blank=True)
    processing_status = models.CharField(max_length=12, default="pending",
                                         choices=[("pending", "pending"), ("ok", "ok"), ("error", "error")])

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "created_at"]

    def save(self, *args, **kwargs):
        new = self._state.adding
        super().save(*args, **kwargs)
        if new and self.file:
            try:
                self.sha256 = _sha256(self.file)
                meta = _ffprobe(self.file.path)
                self.duration_seconds = meta.get("duration")
                self.width = meta.get("width")
                self.height = meta.get("height")
                if not self.taken_at:
                    self.taken_at = timezone.now()

                # Poster @ 1s
                poster_bytes = _ffmpeg_poster(self.file.path, 1.0)
                if poster_bytes:
                    self.poster.save(
                        f"poster-{uuid.uuid4()}.jpg", ContentFile(poster_bytes), save=False)

                # MP4 for playback
                out_mp4 = _ffmpeg_mp4(self.file.path)
                if out_mp4:
                    with open(out_mp4, "rb") as f:
                        self.encoded_mp4.save(
                            f"enc-{uuid.uuid4()}.mp4", ContentFile(f.read()), save=False)

                self.processing_status = "ok"
            except Exception:
                self.processing_status = "error"
            finally:
                super().save(update_fields=[
                    "sha256", "duration_seconds", "width", "height", "taken_at",
                    "poster", "encoded_mp4", "processing_status"
                ])

# --- helpers ---


def _sha256(django_file):
    h = hashlib.sha256()
    for chunk in django_file.chunks():
        h.update(chunk)
    return h.hexdigest()


def _ffprobe(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path
    ])
    data = json.loads(out.decode("utf-8"))
    v = next((s for s in data.get("streams", [])
             if s.get("codec_type") == "video"), {})
    fmt = data.get("format", {})
    return {
        "duration": float(fmt.get("duration")) if fmt.get("duration") else None,
        "width": v.get("width"), "height": v.get("height"),
    }


def _ffmpeg_poster(path, t=1.0):
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "poster.jpg")
        subprocess.check_call(
            ["ffmpeg", "-ss", str(t), "-i", path, "-frames:v", "1", "-q:v", "3", out])
        return pathlib.Path(out).read_bytes()


def _ffmpeg_mp4(path):
    td = tempfile.mkdtemp()
    out = os.path.join(td, "out.mp4")
    subprocess.check_call([
        "ffmpeg", "-y", "-i", path,
        "-movflags", "+faststart", "-vcodec", "libx264", "-preset", "medium", "-crf", "23",
        "-acodec", "aac", "-b:a", "128k", out
    ])
    return out
