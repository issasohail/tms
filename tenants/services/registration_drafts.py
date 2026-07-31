import json
import os
import re
import uuid

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.text import get_valid_filename
from PIL import Image, UnidentifiedImageError

from core.upload_utils import IMAGE_UPLOAD_EXTENSIONS  # Registers HEIC/HEIF when available.
from tenants.models import TemporaryRegistrationUpload


MAX_TEMPORARY_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TEMPORARY_UPLOADS_PER_DRAFT = 80
_DOCUMENT_KINDS = {"photo", "cnic_front", "cnic_back"}
_FORMAT_RULES = {
    "JPEG": ({".jpg", ".jpeg"}, "image/jpeg"),
    "PNG": ({".png"}, "image/png"),
    "WEBP": ({".webp"}, "image/webp"),
    "HEIF": ({".heic", ".heif"}, "image/heif"),
    "HEIC": ({".heic", ".heif"}, "image/heic"),
}
_CLAIMED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}


def parse_draft_id(value):
    try:
        return uuid.UUID(str(value or ""))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError("The browser draft identifier is missing or invalid.")


def classify_registration_field(field_name):
    field_name = str(field_name or "")
    if field_name in _DOCUMENT_KINDS:
        return "main", field_name

    match = re.fullmatch(r"family-(\d{1,2})-(photo|cnic_front|cnic_back)", field_name)
    if match and int(match.group(1)) < 20:
        return "family", match.group(2)

    match = re.fullmatch(
        r"(proposer|seconder|witness1|witness2)-(photo|cnic_front|cnic_back)",
        field_name,
    )
    if match:
        return match.group(1), match.group(2)

    match = re.fullmatch(r"family_update-(\d+)-photo", field_name)
    if match:
        return f"family_update:{match.group(1)}", "photo"

    raise ValidationError("This registration document field is not supported.")


def _validate_filename(upload):
    original = str(getattr(upload, "name", "") or "")
    if (
        not original
        or "\x00" in original
        or "/" in original
        or "\\" in original
        or original in {".", ".."}
        or os.path.basename(original) != original
    ):
        raise ValidationError("The uploaded filename is invalid.")
    sanitized = get_valid_filename(original)
    if not sanitized or sanitized in {".", ".."}:
        raise ValidationError("The uploaded filename is invalid.")
    return sanitized[:255]


def validate_temporary_image(upload):
    if not upload:
        raise ValidationError("Choose an image to upload.")
    original_name = _validate_filename(upload)
    size = int(getattr(upload, "size", 0) or 0)
    if size <= 0:
        raise ValidationError("The selected image is empty.")
    if size > MAX_TEMPORARY_UPLOAD_BYTES:
        raise ValidationError("The selected image exceeds the 10 MiB limit.")

    extension = os.path.splitext(original_name)[1].lower()
    if extension not in IMAGE_UPLOAD_EXTENSIONS:
        raise ValidationError("Use a JPG, JPEG, PNG, WebP, HEIC, or HEIF image.")
    claimed_type = str(getattr(upload, "content_type", "") or "").split(";", 1)[0].lower()
    if claimed_type not in _CLAIMED_IMAGE_TYPES:
        raise ValidationError("The uploaded file does not have a supported image content type.")

    try:
        upload.seek(0)
        with Image.open(upload) as image:
            detected_format = str(image.format or "").upper()
            image.verify()
        upload.seek(0)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationError("The uploaded file is not a valid supported image.")

    rule = _FORMAT_RULES.get(detected_format)
    if not rule or extension not in rule[0]:
        raise ValidationError("The image content does not match its filename extension.")
    detected_type = rule[1]
    if claimed_type != detected_type and not (
        detected_format in {"HEIF", "HEIC"}
        and claimed_type in {"image/heic", "image/heif"}
    ):
        raise ValidationError("The declared and detected image content types do not match.")
    return original_name, detected_type, size


def save_temporary_upload(
    *, tenant, draft_id, field_name, upload, replace_public_id=None
):
    draft_id = parse_draft_id(draft_id)
    _scope, document_kind = classify_registration_field(field_name)
    original_name, detected_type, size = validate_temporary_image(upload)
    if TemporaryRegistrationUpload.objects.filter(
        tenant=tenant, draft_id=draft_id, expires_at__gt=timezone.now()
    ).count() >= MAX_TEMPORARY_UPLOADS_PER_DRAFT:
        raise ValidationError("This draft already has the maximum number of documents.")

    item = TemporaryRegistrationUpload.objects.create(
        tenant=tenant,
        draft_id=draft_id,
        form_field_name=field_name,
        document_kind=document_kind,
        original_filename=original_name,
        detected_content_type=detected_type,
        size=size,
        file=upload,
    )

    if replace_public_id:
        try:
            replace_id = uuid.UUID(str(replace_public_id))
        except (TypeError, ValueError, AttributeError):
            replace_id = None
        if replace_id:
            previous = TemporaryRegistrationUpload.objects.filter(
                tenant=tenant,
                draft_id=draft_id,
                public_id=replace_id,
            ).exclude(pk=item.pk).first()
            if previous:
                previous.delete()
    return item


def temporary_upload_rate_allowed(tenant_id, client_ip, purpose, limit):
    key = f"tenant-registration-draft:{purpose}:{tenant_id}:{client_ip or 'unknown'}"
    count = int(cache.get(key, 0) or 0)
    if count >= limit:
        return False
    cache.set(key, count + 1, 60 * 60)
    return True


def verified_temporary_uploads(tenant, draft_id, raw_mapping):
    draft_id = parse_draft_id(draft_id)
    try:
        mapping = json.loads(raw_mapping or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ValidationError("The saved document list is invalid.")
    if not isinstance(mapping, dict) or len(mapping) > MAX_TEMPORARY_UPLOADS_PER_DRAFT:
        raise ValidationError("The saved document list is invalid.")

    normalized = {}
    public_ids = []
    for requested_field, public_id in mapping.items():
        classify_registration_field(requested_field)
        try:
            parsed_id = uuid.UUID(str(public_id))
        except (TypeError, ValueError, AttributeError):
            raise ValidationError("A saved document identifier is invalid.")
        if parsed_id in public_ids:
            raise ValidationError("A saved document cannot be attached more than once.")
        normalized[requested_field] = parsed_id
        public_ids.append(parsed_id)

    items = {
        item.public_id: item
        for item in TemporaryRegistrationUpload.objects.filter(
            tenant=tenant,
            draft_id=draft_id,
            public_id__in=public_ids,
            expires_at__gt=timezone.now(),
        )
    }
    if len(items) != len(public_ids):
        raise ValidationError("One or more saved documents are missing, expired, or unauthorized.")

    verified = {}
    for requested_field, public_id in normalized.items():
        item = items[public_id]
        requested_scope, requested_kind = classify_registration_field(requested_field)
        stored_scope, stored_kind = classify_registration_field(item.form_field_name)
        same_scope = requested_scope == stored_scope or (
            requested_scope == stored_scope == "family"
        )
        if not same_scope or requested_kind != stored_kind or item.document_kind != requested_kind:
            raise ValidationError("A saved document does not belong to the requested field.")
        verified[requested_field] = item
    return draft_id, verified


def content_file_from_temporary_upload(item):
    item.file.open("rb")
    try:
        content = ContentFile(item.file.read(), name=item.original_filename)
    finally:
        item.file.close()
    return content


def delete_draft_uploads(tenant_id, draft_id):
    for item in TemporaryRegistrationUpload.objects.filter(
        tenant_id=tenant_id, draft_id=draft_id
    ).iterator():
        item.delete()


def cleanup_expired_temporary_uploads(now=None):
    now = now or timezone.now()
    count = 0
    for item in TemporaryRegistrationUpload.objects.filter(expires_at__lte=now).iterator():
        item.delete()
        count += 1
    return count
