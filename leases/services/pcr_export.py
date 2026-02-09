import io

from django.core.files.base import ContentFile
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from PIL import Image
from reportlab.graphics.barcode import qr
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing


def export_pcr_to_pdf(pcr, stream_url_for_video):
    """
    stream_url_for_video(video: PCRVideo) -> str
    Provide a function that returns a URL to play video (e.g., encoded_mp4.url or a presigned S3 URL).
    """
    lease = pcr.lease  # adjust field names as needed
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4

    def header():
        c.setFont("Helvetica-Bold", 12)
        c.drawString(20*mm, H-20*mm, "Move-in Condition Report")
        c.setFont("Helvetica", 10)
        # Replace/access tenant/property fields per your Lease model
        c.drawString(20*mm, H-27*mm,
                     f"Tenant: {getattr(lease, 'tenant_name', 'Tenant')}")
        c.drawString(
            20*mm, H-33*mm, f"Property: {getattr(lease, 'property_name', 'Property')}  Unit: {getattr(lease, 'unit_number', '')}")
        c.drawString(
            20*mm, H-39*mm, f"Lease: {getattr(lease, 'start_date', '')} → {getattr(lease, 'end_date', '')}")

    # Pages for PHOTOS
    photos = list(pcr.photos.all())
    total = len(photos) + pcr.videos.count()
    idx = 0

    for photo in photos:
        idx += 1
        c.setTitle(f"PCR Lease #{pcr.lease_id}")
        header()

        # Fit photo
        img_pil = Image.open(photo.image)
        iw, ih = img_pil.size
        img = ImageReader(img_pil)
        max_w, max_h = W - 40*mm, H - 110*mm
        scale = min(max_w/iw, max_h/ih)
        c.drawImage(img, 20*mm, 60*mm, iw*scale, ih*scale,
                    preserveAspectRatio=True, anchor='sw')

        # Caption
        c.setFont("Helvetica", 10)
        cap = f"{photo.room or 'Area'} — {photo.comment or 'No comment'} — Taken: {photo.taken_at:%Y-%m-%d %H:%M}"
        c.drawString(20*mm, 55*mm, cap)

        # Signature box
        c.rect(20*mm, 20*mm, W-40*mm, 25*mm)
        c.drawString(22*mm, 40*mm, "Tenant signature:")
        c.drawRightString(W-20*mm, 22*mm, f"Page {idx}/{total}")
        c.showPage()

    # Pages for VIDEOS (poster + QR)
    for video in pcr.videos.all():
        idx += 1
        header()
        # Poster image
        if video.poster:
            img_pil = Image.open(video.poster)
            iw, ih = img_pil.size
            img = ImageReader(img_pil)
            max_w, max_h = W - 40*mm, H - 120*mm
            scale = min(max_w/iw, max_h/ih)
            c.drawImage(img, 20*mm, 70*mm, iw*scale, ih*scale,
                        preserveAspectRatio=True, anchor='sw')

        # Caption/meta
        c.setFont("Helvetica", 10)
        cap = f"{video.room or 'Area'} — {video.comment or 'No comment'}"
        meta = f"Taken: {video.taken_at:%Y-%m-%d %H:%M} • Duration: {int(video.duration_seconds or 0)}s • SHA-256: {(video.sha256 or '')[:16]}…"
        c.drawString(20*mm, 62*mm, cap)
        c.setFont("Helvetica", 9)
        c.drawString(20*mm, 56*mm, meta)

        # QR code for playback
        url = stream_url_for_video(video)
        # new (no external qrcode lib)
        code = qr.QrCodeWidget(url)
        bounds = code.getBounds()
        size = 30*mm
        w, h = bounds[2] - bounds[0], bounds[3] - bounds[1]
        d = Drawing(size, size, transform=[size/w, 0, 0, size/h, 0, 0])
        d.add(code)
        renderPDF.draw(d, c, W - 20*mm - size, 60*mm)

        c.setFont("Helvetica", 8)
        c.drawRightString(W-20*mm, 55*mm, url)

        # Signature box
        c.rect(20*mm, 20*mm, W-40*mm, 25*mm)
        c.setFont("Helvetica", 10)
        c.drawString(22*mm, 40*mm, "Tenant signature:")
        c.drawRightString(W-20*mm, 22*mm, f"Page {idx}/{total}")
        c.showPage()

    c.save()
    buf.seek(0)
    pcr.compiled_pdf.save("report.pdf", ContentFile(buf.read()), save=True)
    return pcr.compiled_pdf
