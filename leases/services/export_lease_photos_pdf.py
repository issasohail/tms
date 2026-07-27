# leases/services/export_lease_photos_pdf.py
import io
import logging
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

logger = logging.getLogger(__name__)


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


def export_lease_photos_pdf(
    lease,
    layout="4up",
    photos_qs=None,
    package_mode=False,
    history=None,
    section_title=None,
):
    media_qs = (
        photos_qs
        if photos_qs is not None
        else lease.media.filter(media_type="image").order_by(
            "sort_order", "created_at", "id"
        )
    )
    media = []
    for item in media_qs:
        if not item.file or not item.file.name:
            continue
        try:
            if not default_storage.exists(item.file.name):
                logger.warning(
                    "Skipping missing lease photo media_id=%s lease_id=%s",
                    item.pk,
                    lease.pk,
                )
                continue
            media.append((item, _image_reader(item)))
        except Exception as exc:
            logger.warning(
                "Skipping unreadable lease photo media_id=%s lease_id=%s: %s",
                item.pk,
                lease.pk,
                exc.__class__.__name__,
            )
    if not media:
        return None, None

    layout = layout if layout in {"1up", "2up", "4up"} else "4up"
    cols, rows = _layout_grid(layout)
    per_page = cols * rows

    tenant = _tenant_name(lease)
    prop = _property_name(lease)
    unit = _unit_name(lease)
    period_source = history or lease
    period = (
        f"{getattr(period_source, 'start_date', '')} "
        f"to {getattr(period_source, 'end_date', '')}"
    )
    folder_label = _folder_name_for_lease(lease)
    generated = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    total_pages = ceil(len(media) / per_page)

    left_margin = 12 * mm
    right_margin = 12 * mm
    header_y = H - 12 * mm
    content_top = H - (38 * mm if package_mode else 24 * mm)
    content_bottom = 32 * mm if package_mode else 26 * mm
    signature_y = 22 * mm if package_mode else 17 * mm
    footer_y = 8 * mm
    gap = 6 * mm
    initial_h = 7 * mm
    caption_h = 9 * mm if package_mode else 0

    usable_w = W - left_margin - right_margin
    usable_h = content_top - content_bottom
    slot_w = (usable_w - (cols - 1) * gap) / cols
    slot_h = (usable_h - (rows - 1) * gap) / rows

    def draw_header():
        if package_mode:
            c.setFont("Helvetica-Bold", 12)
            c.drawCentredString(
                W / 2,
                H - 10 * mm,
                section_title or "ANNEXURE - LEASE CONDITION PHOTOGRAPHS",
            )
            history_label = (
                getattr(history, "history_label", "")
                if history is not None
                else ""
            )
            c.setFont("Helvetica", 7.5)
            c.drawCentredString(
                W / 2,
                H - 15 * mm,
                (
                    f"Tenant: {tenant} | Property: {prop} | Unit: {unit} | "
                    f"{history_label or 'Agreement'}"
                )[:140],
            )
            c.drawCentredString(
                W / 2,
                H - 19 * mm,
                (
                    f"Agreement period: {period} | Total photographs: {len(media)} | "
                    f"Generated: {generated}"
                )[:140],
            )
            c.line(left_margin, H - 22 * mm, W - right_margin, H - 22 * mm)
        else:
            c.setFont("Helvetica", 8)
            c.drawString(left_margin, header_y, f"Tenant: {tenant}"[:55])
            c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(W / 2, header_y, f"Lease Photos for {prop}_{unit}"[:80])
            c.setFont("Helvetica", 8)
            c.drawRightString(W - right_margin, header_y, f"Lease Period: {period}"[:55])
            c.line(left_margin, header_y - 3 * mm, W - right_margin, header_y - 3 * mm)

    def draw_footer(page_no):
        if package_mode:
            return
        c.setFont("Helvetica", 7)
        c.drawString(left_margin, footer_y, generated)
        c.drawCentredString(W / 2, footer_y, folder_label[:100])
        c.drawRightString(W - right_margin, footer_y, f"Page {page_no}/{total_pages}")

    for page_index in range(total_pages):
        page_no = page_index + 1
        draw_header()
        page_media = media[page_index * per_page:(page_index + 1) * per_page]

        for index, (item, image_reader) in enumerate(page_media):
            col = index % cols
            row = index // cols
            x = left_margin + col * (slot_w + gap)
            y_top = content_top - row * (slot_h + gap)
            y = y_top - slot_h
            image_h = slot_h - initial_h - caption_h
            c.drawImage(
                image_reader,
                x,
                y + initial_h + caption_h,
                slot_w,
                image_h,
                preserveAspectRatio=True,
                anchor="c",
            )

            c.setFont("Helvetica", 8)
            c.drawString(x, y + 1.5 * mm, "Tenant Initial: __________________")
            if package_mode:
                caption = " - ".join(
                    value.strip()
                    for value in (item.title or "", item.description or "")
                    if value and value.strip()
                )
                if caption:
                    c.setFont("Helvetica", 7)
                    c.drawString(
                        x,
                        y + initial_h + 1.5 * mm,
                        caption[:95],
                    )

        c.setFont("Helvetica", 8)
        c.drawString(left_margin, signature_y, "Tenant Signature: ______________________________")
        draw_footer(page_no)
        c.showPage()

    c.setTitle(f"{folder_label}_Photos")
    c.save()
    buf.seek(0)

    base = f"{folder_label}_Photos.pdf"
    return base, ContentFile(buf.read(), name=base)
