import builtins
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from core.upload_utils import compress_instance_file_field
from core.utils.text import normalize_title_fields, smart_title


class ExpenseDistribution(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = smart_title(self.name)
        super().save(*args, **kwargs)


class Property(models.Model):
    PROPERTY_TYPES = (
        ("apartment", "Apartment"),
        ("house", "House"),
        ("condo", "Condo"),
        ("commercial", "Commercial"),
    )

    property_name = models.CharField(
        max_length=100, verbose_name="Property Name", db_column="name"
    )
    owner_prefix = models.CharField(max_length=5, null=True, blank=True, default="Mr.")
    owner_name = models.CharField(max_length=100)
    owner_father_name = models.CharField(max_length=100, blank=True, null=True)
    relation = models.CharField(max_length=10, null=True, blank=True, default="S/O")
    owner_phone = models.CharField(max_length=20, blank=True, null=True)
    owner_address = models.CharField(max_length=200, blank=True, null=True)
    owner_cnic = models.CharField(max_length=15)
    owner_phone = models.CharField(max_length=25, blank=True, null=True)
    caretaker_prefix = models.CharField(
        max_length=5, null=True, blank=True, default="Mr."
    )
    caretaker_prefix = models.CharField(
        max_length=5, null=True, blank=True, default="Mr."
    )
    caretaker_name = models.CharField(max_length=100, blank=True, null=True)
    caretaker_father_name = models.CharField(max_length=100, blank=True, null=True)
    caretaker_relation = models.CharField(
        max_length=10, null=True, blank=True, default="S/O"
    )
    caretaker_address = models.CharField(max_length=200, blank=True, null=True)
    caretaker_cnic = models.CharField(max_length=15, blank=True, null=True)
    caretaker_phone = models.CharField(max_length=25, blank=True, null=True)
    property_address1 = models.CharField(max_length=200, blank=True, null=True)
    property_address2 = models.TextField(max_length=100, blank=True, null=True)
    property_city = models.CharField(max_length=20, blank=True, null=True)
    property_state = models.CharField(max_length=10, blank=True, null=True)
    property_zipcode = models.CharField(max_length=10, blank=True, null=True)
    bank_account_details = models.TextField(
        blank=True,
        null=True,
        help_text="Default bank account/payment instructions for this property.",
    )

    type = models.CharField(max_length=50)  # with exactly these names
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPES)
    total_units = models.PositiveIntegerField()
    description = models.CharField(max_length=1000, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.property_name} "

    def full_address(self):
        parts = [self.property_address1]

        if self.property_address2.strip():  # Add only if not blank or whitespace
            parts.append(self.property_address2)

        parts.append(self.property_city)
        parts.append(self.property_state)
        parts.append(self.property_zipcode)

        return ", ".join(parts)

    def save(self, *args, **kwargs):
        normalize_title_fields(
            self,
            (
                "property_name",
                "owner_name",
                "owner_father_name",
                "caretaker_name",
                "caretaker_father_name",
                "property_city",
                "property_state",
            ),
        )
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["property_name"]
        verbose_name_plural = "Properties"


class Unit(models.Model):
    UNIT_STATUS = [
        ("vacant", "Vacant"),
        ("occupied", "Occupied"),
        ("maintenance", "Maintenance"),
    ]

    property = models.ForeignKey(
        "Property", on_delete=models.CASCADE, related_name="units"
    )
    interest_type = models.ForeignKey(
        "tenants.TenantInterestType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="units",
        help_text="Used to match vacant units with interested tenants.",
    )
    unit_number = models.CharField(max_length=20)
    electric_meter_num = models.CharField(
        max_length=20, null=True, blank=True, default="0000000000"
    )
    is_smart_meter = models.BooleanField(default=False)
    gas_meter_num = models.CharField(
        max_length=20, null=True, blank=True, default="12345"
    )
    society_maintenance = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default="1200.00"
    )
    water_charges = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, default="6000.00"
    )
    use_property_bank_account = models.BooleanField(
        default=True,
        help_text="Use the property's default bank account/payment instructions.",
    )
    bank_account_details = models.TextField(
        blank=True,
        null=True,
        help_text="Optional unit-specific bank account/payment instructions.",
    )
    monthly_rent = models.DecimalField(
        # Add this if missing
        decimal_places=2,
        default="25000.00",
        max_digits=10,
    )
    security_requires = models.CharField(
        max_length=20, null=True, blank=True, default="Two Months"
    )
    ceiling_fan = models.IntegerField(blank=True, null=True, default=3)
    exhaust_fan = models.IntegerField(blank=True, null=True, default=3)
    ceiling_lights = models.IntegerField(blank=True, null=True, default=16)
    stove = models.IntegerField(blank=True, null=True, default=0)
    keys = models.IntegerField(blank=True, null=True, default=2)
    paint_condition = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        default="New Paint with no marks or water seapage",
    )
    wardrobes = models.IntegerField(blank=True, null=True, default=2)
    bedrooms = models.IntegerField(blank=True, null=True, default=2)
    bathrooms = models.IntegerField(blank=True, null=True, default=2)
    kitchens = models.IntegerField(blank=True, null=True, default=1)
    hall = models.IntegerField(blank=True, null=True, default=1)
    square_footage = models.IntegerField(null=True, blank=True)
    comments = models.CharField(
        max_length=100, null=True, blank=True, default="Good Condition."
    )
    status = models.CharField(
        max_length=20, choices=UNIT_STATUS, default="vacant"
    )  # Add this if missing
    show_publicly = models.BooleanField(
        default=True,
        verbose_name="Show in Public Vacancy List",
        help_text="If unchecked, this unit will not appear in WhatsApp/public vacancy lists.",
    )

    def __str__(self):
        return f"{self.property.property_name}-{self.unit_number}"


MEDIA_FILE_EXTENSIONS = [
    "jpg",
    "jpeg",
    "png",
    "webp",
    "heic",
    "heif",
    "pdf",
    "mp4",
    "mov",
    "avi",
    "mkv",
]
IMAGE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}


def _safe_filename(value):
    value = os.path.basename(value or "file")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in value)
    return safe.strip("-") or "file"


def _name_part(value, fallback="item", max_len=42):
    value = str(value or fallback).strip()
    safe = "".join(ch if ch.isalnum() else "-" for ch in value)
    safe = "-".join(part for part in safe.split("-") if part)
    return (safe or fallback)[:max_len]


def _media_date(instance):
    dt = getattr(instance, "uploaded_at", None) or timezone.now()
    return timezone.localtime(dt).strftime("%Y%m%d")


def _media_serial(instance):
    serial = getattr(instance, "_media_serial", None)
    if serial:
        return int(serial)
    if isinstance(instance, PropertyMedia):
        return instance.property.media_files.count() + 1
    if isinstance(instance, UnitMedia):
        return instance.unit.media_files.count() + 1
    return 1


def _media_base_filename(instance):
    cached_name = getattr(instance, "_formatted_base_filename", "")
    if cached_name:
        return cached_name

    date_part = _media_date(instance)
    if isinstance(instance, PropertyMedia):
        return (
            f"{_name_part(instance.property.property_name, 'property')}_"
            f"{date_part}_{_media_serial(instance):04d}"
        )
    if isinstance(instance, UnitMedia):
        unit = instance.unit
        return (
            f"{_name_part(unit.property.property_name, 'property')}-"
            f"{_name_part(unit.unit_number, 'unit')}_"
            f"{date_part}_{_media_serial(instance):04d}"
        )
    return f"media_{date_part}_{_media_serial(instance):04d}"


def _media_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    folder = instance.storage_folder
    return f"properties/{folder}/original/{_media_base_filename(instance)}{ext}"


def _media_stamped_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or ".jpg"
    folder = instance.storage_folder
    return f"properties/{folder}/stamped/{_media_base_filename(instance)}-stamped{ext}"


def _media_thumbnail_path(instance, filename):
    folder = instance.storage_folder
    return f"properties/{folder}/thumbs/{_media_base_filename(instance)}-thumb.jpg"


class BasePropertyMedia(models.Model):
    file = models.FileField(
        upload_to=_media_upload_path,
        validators=[FileExtensionValidator(MEDIA_FILE_EXTENSIONS)],
        max_length=255,
    )
    file_type = models.CharField(
        max_length=10,
        choices=[("image", "Image"), ("video", "Video"), ("file", "File")],
        default="file",
    )
    description = models.CharField(max_length=300, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    uploaded_at = models.DateTimeField(default=timezone.now)
    stamped_file = models.ImageField(
        upload_to=_media_stamped_path,
        blank=True,
        null=True,
        editable=False,
        max_length=255,
    )
    thumbnail = models.ImageField(
        upload_to=_media_thumbnail_path,
        blank=True,
        null=True,
        editable=False,
        max_length=255,
    )
    is_active = models.BooleanField(default=True)
    original_filename = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["sort_order", "-uploaded_at"]

    @property
    def display_url(self):
        if self.file_type == "image" and self.stamped_file:
            return self.stamped_file.url
        return self.file.url

    @property
    def display_filename(self):
        return os.path.basename(self.file.name or self.original_filename or "file")

    @property
    def storage_folder(self):
        raise NotImplementedError

    def _set_file_type(self):
        ext = os.path.splitext(self.file.name or "")[1].lower()
        if ext in IMAGE_FILE_EXTENSIONS:
            self.file_type = "image"
        elif ext in VIDEO_FILE_EXTENSIONS:
            self.file_type = "video"
        else:
            self.file_type = "file"

    def _build_image_derivatives(self):
        if self.file_type != "image" or not self.file:
            return

        self.file.open("rb")
        with Image.open(self.file) as image:
            image = image.convert("RGB")
            width, height = image.size
            footer_height = max(34, min(70, height // 6))
            stamped = Image.new("RGB", (width, height + footer_height), "white")
            stamped.paste(image, (0, 0))

            draw = ImageDraw.Draw(stamped)
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
            text = self.footer_text[:140]
            draw.text((10, height + 8), text, fill="black", font=font)

            stamped_buffer = ContentFile(b"")
            import io

            buffer = io.BytesIO()
            stamped.save(buffer, format="JPEG", quality=90)
            stamped_buffer = ContentFile(buffer.getvalue())
            base_filename = _media_base_filename(self)
            self.stamped_file.save(
                f"{base_filename}-stamped.jpg", stamped_buffer, save=False
            )

            thumb = stamped.copy()
            thumb.thumbnail((360, 260))
            thumb_buffer = io.BytesIO()
            thumb.save(thumb_buffer, format="JPEG", quality=85)
            self.thumbnail.save(
                f"{base_filename}-thumb.jpg",
                ContentFile(thumb_buffer.getvalue()),
                save=False,
            )

    @property
    def footer_text(self):
        return f"{timezone.localtime(self.uploaded_at):%Y-%m-%d %H:%M}  {self.description or self.original_filename}"

    def save(self, *args, **kwargs):
        adding = self._state.adding
        if adding and not self.original_filename:
            self.original_filename = _safe_filename(getattr(self.file, "name", ""))
        compress_instance_file_field(self, "file")
        self._set_file_type()
        super().save(*args, **kwargs)
        if self.file_type == "image" and (
            adding or not self.stamped_file or not self.thumbnail
        ):
            self._build_image_derivatives()
            super().save(
                update_fields=["stamped_file", "thumbnail", "file_type", "updated_at"]
            )

    def refresh_image_derivatives(self):
        if self.file_type != "image":
            return
        self.stamped_file = None
        self.thumbnail = None
        self._formatted_base_filename = os.path.splitext(self.display_filename)[0]
        self._build_image_derivatives()
        self.save(update_fields=["stamped_file", "thumbnail", "updated_at"])


class PropertyMedia(BasePropertyMedia):
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="media_files",
    )

    @builtins.property
    def storage_folder(self):
        return _name_part(self.property.property_name, f"property-{self.property_id}")

    @builtins.property
    def footer_text(self):
        return f"{timezone.localtime(self.uploaded_at):%Y-%m-%d %H:%M}  {self.property.property_name}  {self.description or self.original_filename}"

    def __str__(self):
        return f"{self.property} - {self.original_filename or self.file.name}"


class UnitMedia(BasePropertyMedia):
    unit = models.ForeignKey(
        Unit,
        on_delete=models.CASCADE,
        related_name="media_files",
    )

    @property
    def storage_folder(self):
        return (
            f"{_name_part(self.unit.property.property_name, 'property')}-"
            f"{_name_part(self.unit.unit_number, f'unit-{self.unit_id}')}"
        )

    @property
    def footer_text(self):
        return f"{timezone.localtime(self.uploaded_at):%Y-%m-%d %H:%M}  {self.unit}  {self.description or self.original_filename}"

    def __str__(self):
        return f"{self.unit} - {self.original_filename or self.file.name}"
