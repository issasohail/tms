# leases/services/export_photos_pdf.py
import io
from django.core.files.base import ContentFile
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image


def export_photos_pdf(pcr):
    """
    Builds a PDF with:
      - Lease/Tenant header (adapt header lines to your Lease fields)
      - One photo per page
      - Caption: room/comment/timestamp
      - Signature box (per page)
    Returns (django.core.files.File-like) ready to save into a FileField.
    """
    lease = pcr.lease

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    photos = list(pcr.photos.all())
    total = len(photos)

    def header():
        c.setFont("Helvetica-Bold", 12)
        c.drawString(20*mm, H-20*mm, "Move-in Condition Photos")
        c.setFont("Helvetica", 10)
        # ↓ Replace these with your actual lease/tenant fields
        # (e.g., lease.tenant.name, lease.unit.unit_number, etc.)
        c.drawString(20*mm, H-27*mm,
                     f"Tenant: {getattr(lease, 'tenant_name', 'Tenant')}")
        c.drawString(
            20*mm, H-33*mm, f"Property: {getattr(lease, 'property_name', 'Property')}  Unit: {getattr(lease, 'unit_number', '')}")
        c.drawString(
            20*mm, H-39*mm, f"Lease: {getattr(lease, 'start_date', '')} → {getattr(lease, 'end_date', '')}")

    for idx, photo in enumerate(photos, start=1):
        c.setTitle(f"Lease #{lease.id} - Move-in Photos")
        header()

        # place the image
        with Image.open(photo.image) as img_pil:
            iw, ih = img_pil.size
            img = ImageReader(img_pil)
        max_w, max_h = W - 40*mm, H - 110*mm
        scale = min(max_w/iw, max_h/ih)
        c.drawImage(img, 20*mm, 60*mm, iw*scale, ih*scale, ...)

        # caption
        c.setFont("Helvetica", 10)
        cap = f"{photo.room or 'Area'} — {photo.comment or 'No comment'} — Taken: {photo.taken_at:%Y-%m-%d %H:%M}"
        c.drawString(20*mm, 55*mm, cap)

        # signature box
        c.rect(20*mm, 20*mm, W-40*mm, 25*mm)
        c.drawString(22*mm, 40*mm, "Tenant signature:")
        c.setFont("Helvetica", 9)
        c.drawRightString(W-20*mm, 22*mm, f"Page {idx}/{total}")

        c.showPage()

    c.save()
    buf.seek(0)
    return ContentFile(buf.read(), name="move_in_photos.pdf")
