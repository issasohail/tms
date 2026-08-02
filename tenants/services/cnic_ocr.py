import logging
import mimetypes
import re
import base64
from datetime import date
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from whatsapp.services.openai_ocr import (
    _date_or_none,
    _normalized_image_data,
    _openai_client,
    _openai_error_kind,
    _parse_json,
    _usage,
)


logger = logging.getLogger(__name__)

# Normalized x, y, width and height for the portrait on a standard CNIC front.
# Adjust these four values when the fallback crop needs to move or resize.
CNIC_PORTRAIT_CROP = (0.71, 0.24, 0.25, 0.55)

CNIC_OCR_PROMPT = """
The first image is intended to be the FRONT and the second image the BACK of the
same Pakistani CNIC. Read only clearly printed information. Pakistani CNIC dates
are printed as DD.MM.YYYY. Read all eight date digits exactly before converting:
the first pair is the day and the second pair is the month. Return every date as
YYYY-MM-DD. For example, printed 05.11.2024 must become 2024-11-05, never
2024-05-11 or 2024-05-01. Recheck the printed issue and expiry dates independently
before returning them; do not swap or drop month digits.

Extract name, father/husband name, gender, country of stay, identity number,
date of birth, date of issue, and date of expiry from the front.
Return the front identity number separately as front_identity_number. On the
back, read back_identity_number from the identity number printed in the TOP
RIGHT corner. Never copy or infer either number from the other image. Return
null for a side when its own printed number is not clearly readable.
On the front, the 13-digit identity number is printed below the label
"Identity Number" in the lower section. Ignore any faded 5-digit card serial
printed near the bottom-left corner; that short serial is not the identity number.
Return portrait_bbox as normalized 0-to-1 x, y, width and height coordinates
covering only the printed portrait photograph on the CNIC front. Return null
when the portrait boundary cannot be identified safely.

On the back, carefully locate the two Urdu address blocks immediately LEFT of
the QR code in the upper half of the card. The UPPER block is the current address
(موجودہ پتہ). The LOWER block is the permanent address (مستقل پتہ). In the
annotated example these are outlined red and blue respectively, but normal
uploads will not contain those colored outlines. Do not swap the two blocks.

Return each address twice: (1) the best exact Urdu-script transcription and (2)
a faithful English transliteration/translation. Preserve every readable house,
street, road, mohalla, colony, post office, village, tehsil and district name.
Read across wrapped lines until the next address block or card element begins.

Give a separate 0-to-1 confidence for each address. Make the best transcription
from visible text even when the scan is imperfect; use null only when no address
characters can be read. Never complete unreadable words by guessing.
Do not treat government text, signatures, return-card instructions, QR content,
or issuing-authority text as an address. Never invent or infer missing words.
Compare the identity number printed on each side. If the normalized 13 digits
match, treat the images as the same card and do not warn about different cards,
even if portrait appearance or image quality differs. Warn about mismatch only
when two clearly readable identity numbers differ.
"""

_NULLABLE_TEXT = {"type": ["string", "null"]}
CNIC_OCR_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["pakistani_cnic", "other", "unknown"],
        },
        "name": _NULLABLE_TEXT,
        "father_name": _NULLABLE_TEXT,
        "gender": {"type": ["string", "null"], "enum": ["M", "F", "O", None]},
        "country_of_stay": _NULLABLE_TEXT,
        "identity_number": _NULLABLE_TEXT,
        "front_identity_number": _NULLABLE_TEXT,
        "back_identity_number": _NULLABLE_TEXT,
        "date_of_birth": _NULLABLE_TEXT,
        "date_of_issue": _NULLABLE_TEXT,
        "date_of_expiry": _NULLABLE_TEXT,
        "portrait_bbox": {
            "type": ["object", "null"],
            "properties": {
                "x": {"type": "number", "minimum": 0, "maximum": 1},
                "y": {"type": "number", "minimum": 0, "maximum": 1},
                "width": {"type": "number", "minimum": 0, "maximum": 1},
                "height": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["x", "y", "width", "height"],
            "additionalProperties": False,
        },
        "temporary_address_urdu": _NULLABLE_TEXT,
        "permanent_address_urdu": _NULLABLE_TEXT,
        "temporary_address_english": _NULLABLE_TEXT,
        "permanent_address_english": _NULLABLE_TEXT,
        "temporary_address_confidence": {
            "type": "number", "minimum": 0, "maximum": 1
        },
        "permanent_address_confidence": {
            "type": "number", "minimum": 0, "maximum": 1
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "document_type", "name", "father_name", "gender", "country_of_stay",
        "identity_number", "front_identity_number", "back_identity_number",
        "date_of_birth", "date_of_issue", "date_of_expiry",
        "portrait_bbox",
        "temporary_address_urdu", "permanent_address_urdu",
        "temporary_address_english", "permanent_address_english", "confidence",
        "temporary_address_confidence", "permanent_address_confidence",
        "warnings",
    ],
    "additionalProperties": False,
}


def extract_cnic_identity(front_file, back_file, model):
    """Return review-only identity suggestions from both sides of a CNIC."""
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return _unavailable("CNIC OCR is unavailable because OPENAI_API_KEY is not configured.")
    if not front_file or not back_file:
        return _unavailable("Choose both CNIC Front and CNIC Back images first.")

    images = []
    front_source = b""
    auto_rotated_sides = []
    address_image_sufficient = False
    for label, file_field in (("front", front_file), ("back", back_file)):
        mime_type = (
            getattr(file_field, "content_type", "")
            or mimetypes.guess_type(getattr(file_field, "name", "") or "")[0]
            or ""
        )
        if not mime_type.startswith("image/"):
            return _unavailable(f"CNIC {label} must be an image file.")
        try:
            file_field.open("rb")
            source = file_field.read()
            file_field.close()
            source, was_auto_rotated = _auto_orient_cnic_source(source)
            if was_auto_rotated:
                auto_rotated_sides.append(label)
            if label == "front":
                front_source = source
            if label == "back":
                with Image.open(BytesIO(source)) as dimension_image:
                    address_image_sufficient = (
                        dimension_image.width >= 600
                        and dimension_image.height >= 350
                    )
            normalized_file = ContentFile(
                source, name=getattr(file_field, "name", f"cnic-{label}.jpg")
            )
            encoded, normalized_mime = _normalized_image_data(normalized_file)
        except Exception:
            logger.warning("CNIC OCR image normalization failed side=%s", label)
            return _unavailable(f"CNIC {label} image could not be prepared for OCR.")
        images.append(
            {
                "type": "input_image",
                "image_url": f"data:{normalized_mime};base64,{encoded}",
                "detail": "high",
            }
        )
        if label == "back":
            enhanced_back_identity = _enhanced_back_identity_image_data(source)
            if enhanced_back_identity:
                images.extend(
                    [
                        {
                            "type": "input_text",
                            "text": (
                                "The next image is an enlarged crop of the top-right of the "
                                "same CNIC back. Read the complete 13-digit value there, "
                                "including its final check digit, as back_identity_number."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": (
                                f"data:image/png;base64,{enhanced_back_identity}"
                            ),
                            "detail": "high",
                        },
                    ]
                )
            enhanced_front = _enhanced_front_image_data(front_source)
            if enhanced_front:
                images.extend(
                    [
                        {
                            "type": "input_text",
                            "text": (
                                "The next image is an enlarged, high-contrast crop of the "
                                "lower middle of the same CNIC front. Read the 13-digit value "
                                "directly below the Identity Number label as "
                                "front_identity_number. Also recheck Date of Birth, Date of "
                                "Issue, and Date of Expiry here. Read all four printed year "
                                "digits exactly; do not replace or infer a year digit."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{enhanced_front}",
                            "detail": "high",
                        },
                    ]
                )
            enhanced = _enhanced_back_image_data(
                ContentFile(source, name=getattr(file_field, "name", "cnic-back.jpg"))
            )
            if enhanced:
                images.extend(
                    [
                        {
                            "type": "input_text",
                            "text": (
                                "The next image is an enlarged, high-contrast crop of the "
                                "same CNIC back. Read the upper Urdu block as current address "
                                "and the lower Urdu block as permanent address. It is supplied "
                                "only to help read the small address text left of the QR code."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{enhanced}",
                            "detail": "high",
                        },
                    ]
                )

    try:
        response = _openai_client().responses.create(
            model=model,
            store=False,
            max_output_tokens=1800,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pakistani_cnic_identity",
                    "strict": True,
                    "schema": CNIC_OCR_SCHEMA,
                }
            },
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": CNIC_OCR_PROMPT.strip()},
                        *images,
                    ],
                }
            ],
        )
    except ImportError:
        return _unavailable("The openai Python package is not installed.")
    except Exception as exc:
        logger.warning("CNIC OCR failed error=%s", _openai_error_kind(exc))
        return _unavailable("CNIC OCR could not read these images. Enter the details manually.")

    result = _parse_json(getattr(response, "output_text", "") or "")
    result["engine"] = "openai"
    result["model"] = model
    result["usage"] = _usage(response)
    try:
        result["confidence"] = max(
            0, min(100, round(float(result.get("confidence") or 0) * 100))
        )
    except (TypeError, ValueError):
        result["confidence"] = 0

    if result.get("document_type") != "pakistani_cnic":
        return {
            **result,
            "fields": {},
            "message": "Both selected images were not recognized as one Pakistani CNIC.",
        }

    warnings = [str(item).strip() for item in result.get("warnings", []) if str(item).strip()]
    if auto_rotated_sides:
        rotated_labels = " and ".join(auto_rotated_sides)
        image_word = "image was" if len(auto_rotated_sides) == 1 else "images were"
        warnings.append(
            f"The CNIC {rotated_labels} {image_word} automatically rotated for reading."
        )
        result["auto_rotated_sides"] = auto_rotated_sides
    front_cnic_digits = re.sub(
        r"\D", "", result.get("front_identity_number") or ""
    )
    back_cnic_digits = re.sub(
        r"\D", "", result.get("back_identity_number") or ""
    )
    if len(front_cnic_digits) != 13:
        return {
            **result,
            "fields": {},
            "message": "The CNIC number on the front could not be read clearly. Upload a clearer front image.",
        }
    if len(back_cnic_digits) != 13:
        return {
            **result,
            "fields": {},
            "message": "The CNIC number at the top-right of the back could not be read clearly. Upload a clearer back image.",
        }
    if front_cnic_digits != back_cnic_digits:
        return {
            **result,
            "fields": {},
            "message": (
                "CNIC verification failed: the number on the back does not match "
                "the number on the front. Upload both sides of the same CNIC."
            ),
        }
    result["cnic_verified"] = True
    cnic_digits = front_cnic_digits
    cnic = (
        f"{cnic_digits[:5]}-{cnic_digits[5:12]}-{cnic_digits[12]}"
        if len(cnic_digits) == 13
        else None
    )
    if result.get("identity_number") and not cnic:
        warnings.append("The identity number was unclear or did not contain 13 digits.")

    dob = _valid_dob(result.get("date_of_birth"))
    issue = _date_or_none(result.get("date_of_issue"))
    expiry = _date_or_none(result.get("date_of_expiry"))
    if issue and expiry and expiry < issue:
        warnings.append("Expiry date is earlier than issue date; both dates were withheld.")
        issue = expiry = None

    temporary_confidence = _confidence(result.get("temporary_address_confidence"))
    permanent_confidence = _confidence(result.get("permanent_address_confidence"))
    temporary_urdu = _text(result.get("temporary_address_urdu"))
    temporary_english = _text(result.get("temporary_address_english"))
    permanent_urdu = _text(result.get("permanent_address_urdu"))
    permanent_english = _text(result.get("permanent_address_english"))
    if not address_image_sufficient:
        warnings.append(
            "The CNIC back image is low resolution; verify the best-effort address transcription."
        )
    if not (temporary_urdu or temporary_english):
        warnings.append("Current address could not be read from the upper address block.")
    elif temporary_confidence < 0.75 or not (temporary_urdu and temporary_english):
        warnings.append("Verify the best-effort current address transcription.")
    if not (permanent_urdu or permanent_english):
        warnings.append("Permanent address could not be read from the lower address block.")
    elif permanent_confidence < 0.75 or not (permanent_urdu and permanent_english):
        warnings.append("Verify the best-effort permanent address transcription.")

    fields = {
        "full_name": _text(result.get("name")),
        "first_name": _text(result.get("name")),
        "last_name": _text(result.get("father_name")),
        "gender": result.get("gender") if result.get("gender") in {"M", "F", "O"} else None,
        "country": _text(result.get("country_of_stay")),
        "cnic": cnic,
        "date_of_birth": dob.isoformat() if dob else None,
        "cnic_issue_date": issue.isoformat() if issue else None,
        "cnic_expiry_date": expiry.isoformat() if expiry else None,
        "temporary_address": temporary_english,
        "permanent_address": permanent_english,
        "temporary_address_urdu": temporary_urdu,
        "permanent_address_urdu": permanent_urdu,
    }
    if not fields["temporary_address"] and not fields["permanent_address"]:
        warnings.append("No personal temporary or permanent address was found on the back.")
    result["fields"] = {key: value for key, value in fields.items() if value}
    result["portrait_data_uri"] = _portrait_data_uri(
        front_source,
        result.get("portrait_bbox"),
    )
    result["warnings"] = list(dict.fromkeys(warnings))
    return result


def _valid_dob(value):
    dob = _date_or_none(value)
    if dob and dob <= date.today() and _age_on(dob, date.today()) <= 120:
        return dob
    return None


def _text(value):
    value = " ".join(str(value or "").split())
    return value or None


def _confidence(value):
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _auto_orient_cnic_source(source):
    """Rotate a sideways CNIC scan into its standard landscape orientation."""
    if not source:
        raise ValueError("empty image")
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass

    try:
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
            if image.height <= image.width:
                return source, False
            image = image.rotate(90, expand=True)
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            output = BytesIO()
            image.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue(), True
    except Exception:
        logger.warning("CNIC OCR auto-orientation was skipped for one image.")
        return source, False


def _enhanced_front_image_data(source):
    """Return an enlarged crop of the front identity-number section."""
    if not source:
        return None
    try:
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened).convert("L")
            width, height = image.size
            image = image.crop(
                (
                    max(0, int(width * 0.25)),
                    max(0, int(height * 0.58)),
                    max(1, int(width * 0.68)),
                    max(1, int(height * 0.96)),
                )
            )
            target_width = max(2200, image.width * 3)
            target_height = round(image.height * target_width / image.width)
            image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            image = ImageOps.autocontrast(image, cutoff=0.5)
            image = ImageEnhance.Contrast(image).enhance(1.2)
            image = ImageEnhance.Sharpness(image).enhance(1.5)
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
        return base64.b64encode(output.getvalue()).decode("ascii")
    except Exception:
        logger.warning("CNIC OCR could not create enhanced front-image copy.")
        return None


def _enhanced_back_identity_image_data(source):
    """Return an enlarged crop of the back identity-number section."""
    if not source:
        return None
    try:
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened).convert("L")
            width, height = image.size
            image = image.crop(
                (
                    max(0, int(width * 0.64)),
                    0,
                    width,
                    max(1, int(height * 0.20)),
                )
            )
            target_width = max(2200, image.width * 3)
            target_height = round(image.height * target_width / image.width)
            image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            image = ImageOps.autocontrast(image, cutoff=0.5)
            image = ImageEnhance.Contrast(image).enhance(1.2)
            image = ImageEnhance.Sharpness(image).enhance(1.5)
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
        return base64.b64encode(output.getvalue()).decode("ascii")
    except Exception:
        logger.warning("CNIC OCR could not create enhanced back identity-number crop.")
        return None


def _enhanced_back_image_data(file_field):
    """Return an enlarged crop containing both Urdu blocks left of the QR code."""
    try:
        file_field.open("rb")
        source = file_field.read()
        file_field.close()
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened).convert("L")
            width, height = image.size
            image = image.crop(
                (
                    max(0, int(width * 0.07)),
                    max(0, int(height * 0.01)),
                    max(1, int(width * 0.79)),
                    max(1, int(height * 0.60)),
                )
            )
            target_width = max(2200, image.width * 3)
            target_height = round(image.height * target_width / image.width)
            image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            image = ImageOps.autocontrast(image, cutoff=1)
            image = ImageEnhance.Contrast(image).enhance(1.35)
            image = image.filter(ImageFilter.SHARPEN)
            output = BytesIO()
            image.save(output, format="PNG", optimize=True)
        return base64.b64encode(output.getvalue()).decode("ascii")
    except Exception:
        logger.warning("CNIC OCR could not create enhanced back-image crop.")
        return None


def _portrait_data_uri(source, bbox=None):
    """Return a compact JPEG portrait crop from the CNIC front."""
    if not source:
        return ""
    try:
        source, _was_auto_rotated = _auto_orient_cnic_source(source)
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            default_x, default_y, default_width, default_height = CNIC_PORTRAIT_CROP
            values = bbox if isinstance(bbox, dict) else {}
            try:
                x = float(values.get("x"))
                y = float(values.get("y"))
                box_width = float(values.get("width"))
                box_height = float(values.get("height"))
            except (TypeError, ValueError):
                x, y, box_width, box_height = CNIC_PORTRAIT_CROP
            if not (
                0 <= x < 1
                and 0 <= y < 1
                and 0.08 <= box_width <= 0.55
                and 0.15 <= box_height <= 0.95
                and x + box_width <= 1.02
                and y + box_height <= 1.02
                # Reject OCR boxes that drift into the CNIC text area.
                and abs(x - default_x) <= 0.07
                and abs(y - default_y) <= 0.12
                and abs(box_width - default_width) <= 0.10
                and abs(box_height - default_height) <= 0.18
            ):
                x, y, box_width, box_height = CNIC_PORTRAIT_CROP
            left = max(0, round(x * width))
            top = max(0, round(y * height))
            right = min(width, round((x + box_width) * width))
            bottom = min(height, round((y + box_height) * height))
            if right - left < 40 or bottom - top < 60:
                return ""
            portrait = image.crop((left, top, right, bottom))
            portrait.thumbnail((600, 800), Image.Resampling.LANCZOS)
            output = BytesIO()
            portrait.save(output, format="JPEG", quality=90, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(
            output.getvalue()
        ).decode("ascii")
    except Exception:
        logger.warning("CNIC OCR could not create the portrait crop.")
        return ""


def portrait_content_file(data_uri, filename="cnic-portrait.jpg"):
    """Validate a browser-returned CNIC portrait and convert it to an upload."""
    if not str(data_uri or "").startswith("data:image/jpeg;base64,"):
        return None
    try:
        payload = base64.b64decode(data_uri.split(",", 1)[1], validate=True)
        if not payload or len(payload) > 2 * 1024 * 1024:
            return None
        with Image.open(BytesIO(payload)) as image:
            image.verify()
        return ContentFile(payload, name=filename)
    except Exception:
        return None


def portrait_content_file_from_cnic_front(front_file, filename="cnic-portrait.jpg"):
    """Create the standard portrait fallback directly from a CNIC front file."""
    if not front_file:
        return None
    opened_here = bool(getattr(front_file, "closed", True))
    try:
        if opened_here:
            front_file.open("rb")
        else:
            front_file.seek(0)
        source = front_file.read()
        if opened_here:
            front_file.close()
        else:
            front_file.seek(0)
    except Exception:
        logger.warning("CNIC portrait fallback could not read the front image.")
        return None
    return portrait_content_file(_portrait_data_uri(source), filename=filename)


def _age_on(dob, today):
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _unavailable(message):
    return {
        "engine": "unavailable",
        "fields": {},
        "confidence": 0,
        "warnings": [],
        "message": message,
    }
