# leases/services/export_lease_photos_pdf.py
import io
from math import ceil

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from ..models_lease_photos import _folder_name_for_lease


def _tenant_name(lease):
    tenant = getattr(lease, "tenant", None)
    if tenant and hasattr(tenant, "get_full_name"):
        return tenant.get_full_name() or str(tenant)
    return str(tenant or "Tenant")


def _property_name(lease):
    unit = getattr(lease, "unit", None)
    prop = getattr(unit, "property", None)
    return getattr(prop, "property_name", "") or str(prop or "Property")


def _unit_name(lease):
    unit = getattr(lease, "unit", None)
    return getattr(unit, "unit_number", "") or str(unit or "")


def _layout_grid(layout):
    if layout == "1up":
        return 1, 1
    if layout == "2up":
        return 1, 2
    return 2, 2


def _image_reader(media):
    with default_storage.open(media.file.name, "rb") as fh:
        raw = fh.read()
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    out.seek(0)
    return ImageReader(out)


def export_lease_photos_pdf(lease, layout="4up", photos_qs=None):
    media_qs = photos_qs or lease.media.filter(media_type="image").order_by("sort_order", "created_at", "id")
    media = [
        item for item in media_qs
        if item.file and item.file.name and default_storage.exists(item.file.name)
    ]
    if not media:
        return None, None

    layout = layout if layout in {"1up", "2up", "4up"} else "4up"
    cols, rows = _layout_grid(layout)
    per_page = cols * rows

    tenant = _tenant_name(lease)
    prop = _property_name(lease)
    unit = _unit_name(lease)
    period = f"{getattr(lease, 'start_date', '')} to {getattr(lease, 'end_date', '')}"
    folder_label = _folder_name_for_lease(lease)
    generated = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    total_pages = ceil(len(media) / per_page)

    left_margin = 12 * mm
    right_margin = 12 * mm
    header_y = H - 12 * mm
    content_top = H - 24 * mm
    content_bottom = 26 * mm
    signature_y = 17 * mm
    footer_y = 8 * mm
    gap = 6 * mm
    initial_h = 7 * mm

    usable_w = W - left_margin - right_margin
    usable_h = content_top - content_bottom
    slot_w = (usable_w - (cols - 1) * gap) / cols
    slot_h = (usable_h - (rows - 1) * gap) / rows

    def draw_header():
        c.setFont("Helvetica", 8)
        c.drawString(left_margin, header_y, f"Tenant: {tenant}"[:55])
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(W / 2, header_y, f"Lease Photos for {prop}_{unit}"[:80])
        c.setFont("Helvetica", 8)
        c.drawRightString(W - right_margin, header_y, f"Lease Period: {period}"[:55])
        c.line(left_margin, header_y - 3 * mm, W - right_margin, header_y - 3 * mm)

    def draw_footer(page_no):
        c.setFont("Helvetica", 7)
        c.drawString(left_margin, footer_y, generated)
        c.drawCentredString(W / 2, footer_y, folder_label[:100])
        c.drawRightString(W - right_margin, footer_y, f"Page {page_no}/{total_pages}")

    for page_index in range(total_pages):
        page_no = page_index + 1
        draw_header()
        page_media = media[page_index * per_page:(page_index + 1) * per_page]

        for index, item in enumerate(page_media):
            col = index % cols
            row = index // cols
            x = left_margin + col * (slot_w + gap)
            y_top = content_top - row * (slot_h + gap)
            y = y_top - slot_h
            image_h = slot_h - initial_h

            try:
                c.drawImage(
                    _image_reader(item),
                    x,
                    y + initial_h,
                    slot_w,
                    image_h,
                    preserveAspectRatio=True,
                    anchor="c",
                )
            except Exception:
                c.setFont("Helvetica", 8)
                c.drawCentredString(x + slot_w / 2, y + initial_h + image_h / 2, "Photo file missing")

            c.setFont("Helvetica", 8)
            c.drawString(x, y + 1.5 * mm, "Tenant Initial: __________________")

        c.setFont("Helvetica", 8)
        c.drawString(left_margin, signature_y, "Tenant Signature: ______________________________")
        draw_footer(page_no)
        c.showPage()

    c.setTitle(f"{folder_label}_Photos")
    c.save()
    buf.seek(0)

    base = f"{folder_label}_Photos.pdf"
    return base, ContentFile(buf.read(), name=base)
