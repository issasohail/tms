import os

import fitz
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError


MAX_CNIC_PDF_BYTES = 15 * 1024 * 1024


def is_pdf_upload(upload):
    if not upload:
        return False
    content_type = str(getattr(upload, "content_type", "") or "").lower()
    extension = os.path.splitext(str(getattr(upload, "name", "") or ""))[1].lower()
    return content_type == "application/pdf" or extension == ".pdf"


def cnic_pdf_first_page_to_jpeg(upload):
    """Render the first PDF page to a browser/editor-ready JPEG upload."""
    if not is_pdf_upload(upload):
        return upload

    size = getattr(upload, "size", None)
    if size is not None and size > MAX_CNIC_PDF_BYTES:
        raise ValidationError("CNIC PDF must be 15 MB or smaller.")

    try:
        upload.seek(0)
        payload = upload.read(MAX_CNIC_PDF_BYTES + 1)
        if len(payload) > MAX_CNIC_PDF_BYTES:
            raise ValidationError("CNIC PDF must be 15 MB or smaller.")
        document = fitz.open(stream=payload, filetype="pdf")
        try:
            if document.page_count < 1:
                raise ValidationError("The CNIC PDF has no pages.")
            page = document.load_page(0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            jpeg = pixmap.tobytes("jpeg", jpg_quality=92)
        finally:
            document.close()
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("The CNIC PDF could not be read. Please choose a valid PDF.") from exc
    finally:
        try:
            upload.seek(0)
        except (AttributeError, OSError):
            pass

    stem = os.path.splitext(os.path.basename(str(getattr(upload, "name", "cnic"))))[0]
    converted = ContentFile(jpeg, name=f"{stem or 'cnic'}-page-1.jpg")
    converted.content_type = "image/jpeg"
    return converted
