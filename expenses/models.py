from PIL import Image, ImageDraw, ImageFont, ImageOps
from builtins import property as builtin_property  # <-- alias built-in decorator
import builtins  # <-- add this
from invoices.models import ItemCategory  # shared invoice categories
from datetime import datetime
from django.utils.text import slugify
from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import os
from django.db import models
from django.core.validators import MinValueValidator
from properties.models import Property, Unit
from django.utils import timezone
from invoices.models import ItemCategory


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name


class Expense(models.Model):
    property = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name='expenses')
    unit = models.ForeignKey(
        Unit, null=True, blank=True, on_delete=models.SET_NULL)
    category = models.ForeignKey(
        ItemCategory, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    date = models.DateField()
    description = models.TextField(blank=True, null=True)
    receipt = models.FileField(
        upload_to='expense_receipts/', blank=True, null=True)
    is_distributed = models.BooleanField(default=False)
    distribution_method = models.CharField(
        max_length=50,
        choices=[
            ('equal', 'Equal Distribution'),
            ('by_units', 'By Number of Units'),
            ('by_occupants', 'By Number of Occupants'),
            ('by_usage', 'By Usage'),
        ],
        blank=True, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.property.property_name} - {self.description} - {self.amount}"

    @builtin_property  # <-- use the alias
    def unit_label(self) -> str:
        return getattr(self.unit, "unit_number", str(self.unit)) if self.unit_id else "All Unit"

    @builtin_property  # <-- use the alias
    def receipt_urls(self):
        urls = []
        if hasattr(self, 'receipts'):
            for r in self.receipts.all():
                try:
                    if r.image and hasattr(r.image, 'url'):
                        urls.append(r.image.url)
                except Exception:
                    pass
        if not urls and getattr(self, 'receipt', None):
            try:
                urls.append(self.receipt.url)
            except Exception:
                pass
        return urls

    class Meta:
        ordering = ['-date', '-pk']  # avoids UnorderedObjectListWarning


class ExpenseDistribution(models.Model):
    expense = models.ForeignKey(
        Expense, on_delete=models.CASCADE, related_name='distributions')
    unit = models.ForeignKey('properties.Unit', on_delete=models.CASCADE)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    included_in_invoice = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Expense Distribution"
        verbose_name_plural = "Expense Distributions"

    def __str__(self):
        return f"{self.unit} - {self.amount}"


# expenses/models.py  (put near your other imports)

# expenses/models.py


def receipt_upload_path(instance, filename):
    """
    PropertyName_mmddyyyy-sss.jpg
    - PropertyName: letters/numbers only, no spaces (derived from property_name)
    - mmddyyyy: date from the parent Expense (fallback: today)
    - sss: per-expense serial, zero-padded to 3 (001, 002, ...)
    NOTE: images are saved as optimized JPEGs in ExpenseReceipt.save()
    """
    exp = instance.expense

    # Property token: letters/numbers, no spaces (TitleCase if available)
    raw_prop = getattr(getattr(exp, "property", None),
                       "property_name", "") or "Property"
    # slugify -> "my-property"; strip hyphens to get "myproperty"; title-case for readability
    prop_token = slugify(raw_prop).replace("-", "")
    prop_token = prop_token.title() if prop_token else "Property"

    # Date token
    dt = getattr(exp, "date", None) or timezone.now().date()
    date_token = dt.strftime("%m%d%Y")  # mmddyyyy

    # Serial per expense (count existing, add 1)
    existing = instance.__class__.objects.filter(expense=exp).count()
    serial = f"{existing + 1:03d}"

    # We always save JPEG in .save(), so use .jpg extension
    filename = f"{prop_token}_{date_token}-{serial}.jpg"
    return os.path.join("expense_receipts", filename)


def _load_font(size: int):
    """Try common fonts; fall back to default."""
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("DejaVuSans.ttf", size)
        except Exception:
            return ImageFont.load_default()


def _stamp_timestamp(img: Image.Image, text: str) -> Image.Image:
    """
    Draw a timestamp at bottom-right with a semi-transparent padded background.
    Auto-sizes font; never clips.
    """
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Dynamic font size (~3.5% of width; clamp 14..64)
    size = max(14, min(64, int(base.width * 0.035)))
    font = _load_font(size)

    # Ensure text fits within 90% width; shrink if needed
    max_w = int(base.width * 0.90)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    while tw > max_w and size > 12:
        size -= 2
        font = _load_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    pad = max(6, size // 3)
    margin = max(8, size // 2)

    # Bottom-right (clamped)
    x = max(margin, base.width - tw - margin)
    y = max(margin, base.height - th - margin)

    # BG box + text
    draw.rectangle([x - pad, y - pad, x + tw + pad,
                   y + th + pad], fill=(0, 0, 0, 150))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    return Image.alpha_composite(base, overlay).convert("RGB")


# expenses/models.py  (inside ExpenseReceipt.save)


def save(self, *args, **kwargs):
    # ... keep your existing pre-processing ...
    super_save = super(ExpenseReceipt, self).save

    if self.image and hasattr(self.image, "file"):
        try:
            self.image.file.seek(0)
        except Exception:
            pass

        img = Image.open(self.image)
        img = ImageOps.exif_transpose(img).convert("RGB")

        if self.add_timestamp:
            # Use the EXPENSE date if available; fall back to today.
            date_for_stamp = getattr(
                getattr(self, "expense", None), "date", None) or timezone.now().date()
            # EXACT format → "Sep, 18, 2025"
            stamp_text = date_for_stamp.strftime("%b, %d, %Y")
            img = _stamp_timestamp(img, stamp_text)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        buf.seek(0)
        self.image.save(self.image.name, ContentFile(buf.read()), save=False)

    super_save(*args, **kwargs)


class ExpenseReceipt(models.Model):
    """
    Multi-image receipts for an Expense.
    """
    expense = models.ForeignKey(
        'Expense', on_delete=models.CASCADE, related_name='receipts')
    image = models.ImageField(upload_to=receipt_upload_path)
    original_name = models.CharField(max_length=255, blank=True)
    comment = models.CharField(max_length=255, blank=True, default="")
    add_timestamp = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        base = os.path.basename(self.image.name or "")
        return base or f"Receipt for Expense #{getattr(self.expense, 'id', '')}"

    def save(self, *args, **kwargs):
        """
        - Cache original filename
        - EXIF auto-rotate, convert to RGB
        - Stamp timestamp (no clipping)
        - Save optimized JPEG
        """
        if hasattr(self.image, "name") and not self.original_name:
            try:
                self.original_name = os.path.basename(self.image.name or "")
            except Exception:
                pass

        super_save = super(ExpenseReceipt, self).save

        if self.image and hasattr(self.image, "file"):
            try:
                self.image.file.seek(0)
            except Exception:
                pass

            img = Image.open(self.image)
            img = ImageOps.exif_transpose(img)  # fix orientation
            img = img.convert("RGB")

            if self.add_timestamp:
                stamp_text = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
                img = _stamp_timestamp(img, stamp_text)

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=92, optimize=True)
            buf.seek(0)
            # Replace uploaded content; final path/name resolved by upload_to
            self.image.save(self.image.name, ContentFile(
                buf.read()), save=False)

        # final write
        super_save(*args, **kwargs)
