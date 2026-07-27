from dataclasses import dataclass
from io import BytesIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone

from leases.models import AgreementSignatureTemplate, LeaseDocument


ESTAMP_CATEGORY = "estamp_paper"
OVERRIDE_PERMISSION = "leases.override_estamp_age"
PAPER_SIZES = {
    "legal": (612.0, 1008.0),
    "letter": (612.0, 792.0),
}


@dataclass(frozen=True)
class EStampStatus:
    document: LeaseDocument | None
    age_days: int | None
    max_age_days: int
    is_over_age: bool
    can_override: bool


def latest_estamp(lease):
    return (
        lease.documents.filter(category=ESTAMP_CATEGORY, is_active=True)
        .order_by("-uploaded_at", "-pk")
        .first()
    )


def estamp_status(lease, user=None, *, today=None, config=None):
    document = latest_estamp(lease)
    config = config or AgreementSignatureTemplate.current()
    max_age_days = max(0, int(getattr(config, "estamp_max_age_days", 30) or 0))
    age_days = None
    if document:
        uploaded_date = timezone.localtime(document.uploaded_at).date()
        age_days = max(0, ((today or timezone.localdate()) - uploaded_date).days)
    is_over_age = bool(document and max_age_days and age_days > max_age_days)
    can_override = bool(
        user
        and getattr(user, "is_authenticated", False)
        and (getattr(user, "is_superuser", False) or user.has_perm(OVERRIDE_PERMISSION))
    )
    return EStampStatus(document, age_days, max_age_days, is_over_age, can_override)


def authorize_estamp(lease, user, *, allow_over_age=False):
    status = estamp_status(lease, user)
    if status.document is None:
        raise ValidationError("No E-Stamp Paper has been uploaded for this lease.")
    if status.is_over_age and not (allow_over_age and status.can_override):
        raise PermissionDenied(
            "This E-Stamp Paper is older than the configured maximum age."
        )
    return status.document


def normalize_estamp_pdf(upload, password=""):
    """Return an unlocked, rewritten PDF without retaining the supplied password."""
    from pypdf import PdfReader, PdfWriter

    try:
        payload = upload.read()
        reader = PdfReader(BytesIO(payload), strict=False)
        if reader.is_encrypted:
            if not password:
                raise ValidationError(
                    "The file is password protected. Please enter the password.",
                    code="password_required",
                )
            try:
                unlocked = reader.decrypt(password)
            except Exception as exc:
                raise ValidationError(
                    "The password is incorrect. Please try again.",
                    code="wrong_password",
                ) from exc
            if not unlocked:
                raise ValidationError(
                    "The password is incorrect. Please try again.",
                    code="wrong_password",
                )
        if not reader.pages:
            raise ValidationError("The uploaded E-Stamp PDF has no pages.")
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        output = BytesIO()
        writer.write(output)
        return ContentFile(output.getvalue(), name=upload.name)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(
            "The E-Stamp file is not a readable PDF or its password is incorrect."
        ) from exc


def _visible_box(page):
    box = page.cropbox or page.mediabox
    width = float(box.right) - float(box.left)
    height = float(box.top) - float(box.bottom)
    if width <= 0 or height <= 0:
        box = page.mediabox
        width = float(box.right) - float(box.left)
        height = float(box.top) - float(box.bottom)
    if width <= 0 or height <= 0:
        raise ValidationError("An E-Stamp page has invalid page dimensions.")
    return box, width, height


def _merge_fitted_page(
    destination,
    source,
    *,
    target_width,
    target_height,
    vertical_alignment="top",
    horizontal_alignment="center",
    bottom_clearance=0,
    fit_height=False,
):
    from copy import deepcopy
    from pypdf import Transformation

    page = deepcopy(source)
    if int(page.get("/Rotate", 0) or 0) % 360:
        page.transfer_rotation_to_content()
    box, source_width, source_height = _visible_box(page)
    available_height = max(1.0, target_height - float(bottom_clearance or 0))
    scale = (
        available_height / source_height
        if fit_height
        else min(target_width / source_width, available_height / source_height)
    )
    draw_width = source_width * scale
    draw_height = source_height * scale
    if horizontal_alignment == "right":
        x = target_width - draw_width
    else:
        x = (target_width - draw_width) / 2
    if vertical_alignment == "bottom":
        y = float(bottom_clearance or 0)
    else:
        y = target_height - draw_height
    transform = (
        Transformation()
        .translate(tx=-float(box.left), ty=-float(box.bottom))
        .scale(sx=scale, sy=scale)
        .translate(tx=x, ty=y)
    )
    destination.merge_transformed_page(page, transform, over=True, expand=False)


def compose_stamped_agreement(
    agreement_pdf,
    estamp_pdf,
    paper_size,
    *,
    stamp_footer_bottom_points=None,
):
    """Compose only core agreement pages onto mapped stamp pages."""
    from pypdf import PdfReader, PdfWriter
    from pypdf._page import PageObject
    from pypdf.generic import RectangleObject

    try:
        target_width, target_height = PAPER_SIZES[paper_size]
    except KeyError as exc:
        raise ValidationError("Paper size must be Legal or Letter.") from exc

    agreement_reader = PdfReader(BytesIO(agreement_pdf), strict=False)
    estamp_reader = PdfReader(BytesIO(estamp_pdf), strict=False)
    if not agreement_reader.pages:
        raise ValidationError("The agreement PDF has no pages.")
    if not estamp_reader.pages:
        raise ValidationError("The E-Stamp PDF has no pages.")
    if stamp_footer_bottom_points is None:
        stamp_footer_bottom_points = 46 if paper_size == "legal" else 28
    stamp_footer_bottom_points = max(
        0.0, min(300.0, float(stamp_footer_bottom_points))
    )

    writer = PdfWriter()
    agreement_page_count = len(agreement_reader.pages)
    for index, agreement_page in enumerate(agreement_reader.pages):
        destination = PageObject.create_blank_page(
            width=target_width, height=target_height
        )
        normalized_box = RectangleObject((0, 0, target_width, target_height))
        destination.mediabox = RectangleObject(normalized_box)
        destination.cropbox = RectangleObject(normalized_box)
        destination.trimbox = RectangleObject(normalized_box)
        destination.bleedbox = RectangleObject(normalized_box)
        destination.artbox = RectangleObject(normalized_box)
        stamp_index = None
        if index == 0:
            stamp_index = 0
        elif (
            len(estamp_reader.pages) > 1
            and index == agreement_page_count - 1
        ):
            stamp_index = 1
        if stamp_index is not None:
            _merge_fitted_page(
                destination,
                estamp_reader.pages[stamp_index],
                target_width=target_width,
                target_height=target_height,
                vertical_alignment="bottom",
                horizontal_alignment="right" if paper_size == "letter" else "center",
                bottom_clearance=stamp_footer_bottom_points,
                fit_height=paper_size == "legal",
            )
        _merge_fitted_page(
            destination,
            agreement_page,
            target_width=target_width,
            target_height=target_height,
        )
        writer.add_page(destination)

    output = BytesIO()
    writer.write(output)
    return output.getvalue()
