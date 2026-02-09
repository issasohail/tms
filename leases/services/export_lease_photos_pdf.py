# leases/services/export_lease_photos_pdf.py
import io
from django.core.files.base import ContentFile
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont
from ..models_lease_photos import safe_name


def export_lease_photos_pdf(lease, photos_qs=None):
    # pull only images from LeaseMedia
    media_qs = photos_qs or lease.media.filter(
        media_type="image").order_by("created_at")
    if not media_qs.exists():
        return None, None

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    # Header strings (robust to missing attrs)
    tenant = (
        getattr(getattr(lease, "tenant", None), "get_full_name", lambda: "")() or
        getattr(lease, "tenant_name", "") or "Tenant"
    )
    prop = (
        getattr(getattr(getattr(lease, "unit", None), "property", None), "property_name", None) or
        getattr(getattr(getattr(lease, "unit", None), "property", None), "name", None) or
        getattr(lease, "property_name", "") or "Property"
    )
    unit = (
        getattr(getattr(lease, "unit", None), "unit_number", None) or
        getattr(lease, "unit_number", "") or ""
    )
    period = f"{getattr(lease, 'start_date', '')} → {getattr(lease, 'end_date', '')}"

    def header():
        c.setFont("Helvetica-Bold", 12)
        c.drawString(20*mm, H-20*mm, "Lease Photos")
        c.setFont("Helvetica", 10)
        c.drawString(20*mm, H-27*mm, f"Tenant: {tenant}")
        c.drawString(20*mm, H-33*mm, f"Property: {prop}  Unit: {unit}")
        c.drawString(20*mm, H-39*mm, f"Lease Period: {period}")

    total = media_qs.count()
    for idx, m in enumerate(media_qs, start=1):
        header()

        # open from the new field m.file
        m.file.open("rb")
        with Image.open(m.file) as pil:
            pil = pil.convert("RGB")
            iw, ih = pil.size

            # add a white footer band (no overlay over pixels)
            footer_h = 50
            new_img = Image.new("RGB", (iw, ih + footer_h), "white")
            new_img.paste(pil, (0, 0))

            draw = ImageDraw.Draw(new_img)
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None

            ts_text = f"Taken: {m.taken_at:%Y-%m-%d %H:%M}" if m.taken_at else "Taken: —"
            text_y = ih + (footer_h - (font.getbbox(ts_text)
                           [3] if font else 12)) // 2
            draw.text((10, text_y), ts_text, fill="black", font=font)

            # place on page with margins
            img = ImageReader(new_img)
            niw, nih = new_img.size
            max_w, max_h = W - 40*mm, H - 110*mm
            scale = min(max_w/niw, max_h/nih)
            c.drawImage(img, 20*mm, 60*mm, niw*scale, nih*scale,
                        preserveAspectRatio=True, anchor='sw')

        # caption + meta
        c.setFont("Helvetica", 10)
        cap = f"{(m.title or 'Photo').strip()}"
        if m.description:
            cap += f" — {m.description.strip()}"
        c.drawString(20*mm, 55*mm, cap[:150])

        c.setFont("Helvetica", 9)
        taken = f"Taken: {m.taken_at:%Y-%m-%d %H:%M}" if m.taken_at else "Taken: —"
        dims = f"Size: {niw}×{nih}px"
        c.drawString(20*mm, 49*mm, f"{taken} • {dims}")

        # signature box
        c.rect(20*mm, 20*mm, W-40*mm, 25*mm)
        c.setFont("Helvetica", 10)
        c.drawString(22*mm, 40*mm, "Tenant signature:")
        c.setFont("Helvetica", 9)
        c.drawRightString(W-20*mm, 22*mm, f"Page {idx}/{total}")
        c.showPage()

    c.setTitle(
        f"{safe_name((tenant or '')[:15])}-Photos-Unit{safe_name(str(unit or ''))}")
    c.save()
    buf.seek(0)

    tenant_short = safe_name((tenant or "Tenant")[:15].strip())
    base = f"{tenant_short}-Photos-Unit#{safe_name(str(unit or ''))}.pdf"
    return base, ContentFile(buf.read(), name=base)
