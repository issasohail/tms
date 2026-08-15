import logging
import mimetypes
import re
import base64
import time
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

# Normalized x, y, width and height for portraits on the current right-photo
# CNIC and the older left-photo card layout.
CNIC_PORTRAIT_CROP = (0.71, 0.24, 0.25, 0.55)
CNIC_LEFT_PORTRAIT_CROP = (0.04, 0.22, 0.27, 0.48)
CNIC_PORTRAIT_CROPS = (CNIC_PORTRAIT_CROP, CNIC_LEFT_PORTRAIT_CROP)
CNIC_ENHANCED_MAX_WIDTH = 2400
CNIC_OCR_MAX_OUTPUT_TOKENS = 1200

CNIC_OCR_PROMPT = """
The first image is intended to be the FRONT and the second image the BACK of the
same Pakistani CNIC, but users may accidentally select them in reverse. Identify
the actual side from the visible card layout, return its supplied image position
as front_image_index and back_image_index, and read fields from the actual side
regardless of upload order. Read only clearly printed information. Pakistani CNIC dates
are printed as DD.MM.YYYY. Read all eight date digits exactly before converting:
the first pair is the day and the second pair is the month. Return every date as
YYYY-MM-DD. For example, printed 05.11.2024 must become 2024-11-05, never
2024-05-11 or 2024-05-01. Recheck the printed issue and expiry dates independently
before returning them; do not swap or drop month digits.

Extract name, father/husband name, gender, country of stay, identity number,
date of birth, date of issue, and date of expiry from the front.
Return the front identity number separately as front_identity_number. On the
back, read back_identity_number from the 13-digit identity number. Newer cards
usually print it in the top-right; older cards may print it on the left below
the small portrait. Ignore the QR code and any separate 12-digit card/QR serial
printed below it. Never copy or infer either number from the other image. Return
null for a side when its own printed number is not clearly readable.
On the front, the 13-digit identity number is printed below the label
"Identity Number" in the lower section. Ignore any faded 5-digit card serial
printed near the bottom-left corner; that short serial is not the identity number.
Return portrait_bbox as normalized 0-to-1 x, y, width and height coordinates
covering only the printed human portrait photograph on the CNIC front. Current
cards normally have the portrait on the RIGHT and a gold electronic chip on the
LEFT; the gold chip is never a portrait. Older green cards have the human
portrait on the LEFT. Return portrait_side as "right" or "left" for the actual
human portrait, and return null only when its side cannot be identified safely.
Return null for portrait_bbox when the exact portrait boundary cannot be identified.

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
        "front_image_index": {
            "type": ["integer", "null"], "enum": [1, 2, None]
        },
        "back_image_index": {
            "type": ["integer", "null"], "enum": [1, 2, None]
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
        "portrait_side": {
            "type": ["string", "null"], "enum": ["left", "right", None]
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
        "document_type", "front_image_index", "back_image_index",
        "name", "father_name", "gender", "country_of_stay",
        "identity_number", "front_identity_number", "back_identity_number",
        "date_of_birth", "date_of_issue", "date_of_expiry",
        "portrait_bbox", "portrait_side",
        "temporary_address_urdu", "permanent_address_urdu",
        "temporary_address_english", "permanent_address_english", "confidence",
        "temporary_address_confidence", "permanent_address_confidence",
        "warnings",
    ],
    "additionalProperties": False,
}

CNIC_NUMBER_RECHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "front_identity_number": _NULLABLE_TEXT,
        "back_identity_number": _NULLABLE_TEXT,
    },
    "required": ["front_identity_number", "back_identity_number"],
    "additionalProperties": False,
}

CNIC_STAGED_FRONT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_side": {
            "type": "string",
            "enum": ["front", "back", "other", "unknown"],
        },
        "name": _NULLABLE_TEXT,
        "father_name": _NULLABLE_TEXT,
        "gender": {"type": ["string", "null"], "enum": ["M", "F", "O", None]},
        "country_of_stay": _NULLABLE_TEXT,
        "front_identity_number": _NULLABLE_TEXT,
        "date_of_birth": _NULLABLE_TEXT,
        "date_of_issue": _NULLABLE_TEXT,
        "date_of_expiry": _NULLABLE_TEXT,
        "portrait_bbox": CNIC_OCR_SCHEMA["properties"]["portrait_bbox"],
        "portrait_side": CNIC_OCR_SCHEMA["properties"]["portrait_side"],
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "document_side", "name", "father_name", "gender", "country_of_stay",
        "front_identity_number", "date_of_birth", "date_of_issue",
        "date_of_expiry", "portrait_bbox", "portrait_side", "confidence",
        "warnings",
    ],
    "additionalProperties": False,
}

CNIC_STAGED_BACK_SCHEMA = {
    "type": "object",
    "properties": {
        "document_side": {
            "type": "string",
            "enum": ["front", "back", "other", "unknown"],
        },
        "back_identity_number": _NULLABLE_TEXT,
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
        "document_side", "back_identity_number", "temporary_address_urdu",
        "permanent_address_urdu", "temporary_address_english",
        "permanent_address_english", "temporary_address_confidence",
        "permanent_address_confidence", "confidence", "warnings",
    ],
    "additionalProperties": False,
}

CNIC_STAGED_NUMBER_SCHEMA = {
    "type": "object",
    "properties": {"identity_number": _NULLABLE_TEXT},
    "required": ["identity_number"],
    "additionalProperties": False,
}

CNIC_STAGED_FRONT_PROMPT = """
This image must be the FRONT of a Pakistani CNIC. Identify the side from the
visible layout; return document_side="front" only when it is truly the front.
Read only clearly printed information. Extract the person's name, father or
husband name, gender, country of stay, the complete 13-digit identity number,
date of birth, date of issue and date of expiry. Dates are printed DD.MM.YYYY;
return them as YYYY-MM-DD after independently rechecking all eight digits.
Return null rather than guessing unreadable text. Ignore any short card serial.
Return portrait_bbox as normalized x, y, width and height around only the human
portrait, plus portrait_side as left or right. A gold electronic chip is not a
portrait. Do not claim that the CNIC is verified; its back will be read later.
"""

CNIC_STAGED_BACK_PROMPT = """
This image must be the BACK of a Pakistani CNIC. Identify the side from the
visible layout; return document_side="back" only when it is truly the back.
Independently read the complete 13-digit identity number printed on this image.
On newer cards it is usually top-right; on older cards it may be on the left
below the small portrait. Ignore the QR code and any separate 12-digit serial.
Read the two Urdu address blocks left of the QR code: the upper block is the
current/temporary address and the lower block is the permanent address. Return
each address as exact Urdu text and faithful English transliteration/translation,
including every readable house, street, village, tehsil and district detail.
Return null rather than guessing unreadable text.
"""


def extract_cnic_front_identity(front_file, model):
    """Read provisional front-side fields; final verification still requires the back."""
    started = time.monotonic()
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return _unavailable("CNIC OCR is unavailable because OPENAI_API_KEY is not configured.")
    if not front_file:
        return _unavailable("Choose the CNIC Front image first.")

    try:
        source, encoded, normalized_mime, metrics = _prepare_staged_cnic_image(
            front_file, "front"
        )
    except ValueError as exc:
        return _unavailable(str(exc))
    normalization_ms = round((time.monotonic() - started) * 1000)
    primary_started = time.monotonic()
    try:
        response = _openai_client().responses.create(
            model=model,
            store=False,
            max_output_tokens=800,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pakistani_cnic_staged_front",
                    "strict": True,
                    "schema": CNIC_STAGED_FRONT_SCHEMA,
                }
            },
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": CNIC_STAGED_FRONT_PROMPT.strip()},
                    {
                        "type": "input_image",
                        "image_url": f"data:{normalized_mime};base64,{encoded}",
                        "detail": "high",
                    },
                ],
            }],
        )
    except ImportError:
        return _unavailable("The openai Python package is not installed.")
    except Exception as exc:
        error_kind = _openai_error_kind(exc)
        logger.warning("CNIC staged front OCR failed error=%s", error_kind)
        return _unavailable(_staged_ocr_error_message(error_kind, "front"))
    primary_ms = round((time.monotonic() - primary_started) * 1000)

    result = _parse_json(getattr(response, "output_text", "") or "")
    result.update(engine="openai", model=model, usage=_usage(response))
    if result.get("document_side") != "front":
        logger.info(
            "CNIC OCR timing phase=front outcome=wrong_side total_ms=%s "
            "normalization_ms=%s primary_ms=%s retry_ms=0 metrics=%s",
            round((time.monotonic() - started) * 1000), normalization_ms,
            primary_ms, metrics,
        )
        return {
            **result,
            "fields": {},
            "message": "The selected image was not recognized as the CNIC front.",
        }

    digits = re.sub(r"\D", "", result.get("front_identity_number") or "")
    retry_ms = 0
    if len(digits) != 13:
        retry_started = time.monotonic()
        digits = _retry_staged_identity_number(source, "front", model)
        retry_ms = round((time.monotonic() - retry_started) * 1000)
    if len(digits) != 13:
        logger.info(
            "CNIC OCR timing phase=front outcome=number_unreadable total_ms=%s "
            "normalization_ms=%s primary_ms=%s retry_ms=%s metrics=%s",
            round((time.monotonic() - started) * 1000), normalization_ms,
            primary_ms, retry_ms, metrics,
        )
        return {
            **result,
            "fields": {},
            "message": "The CNIC number on the front could not be read clearly. Upload a clearer front image.",
        }

    dob = _valid_dob(result.get("date_of_birth"))
    issue = _date_or_none(result.get("date_of_issue"))
    expiry = _date_or_none(result.get("date_of_expiry"))
    warnings = [str(item).strip() for item in result.get("warnings", []) if str(item).strip()]
    if issue and expiry and expiry < issue:
        issue = expiry = None
        warnings.append("Expiry date is earlier than issue date; both dates were withheld.")
    cnic = f"{digits[:5]}-{digits[5:12]}-{digits[12]}"
    fields = {
        "first_name": _text(result.get("name")),
        "last_name": _text(result.get("father_name")),
        "gender": result.get("gender") if result.get("gender") in {"M", "F", "O"} else None,
        "country": _text(result.get("country_of_stay")),
        "nationality": "Pakistani",
        "cnic": cnic,
        "date_of_birth": dob.isoformat() if dob else None,
        "cnic_issue_date": issue.isoformat() if issue else None,
        "cnic_expiry_date": expiry.isoformat() if expiry else None,
    }
    result["fields"] = {key: value for key, value in fields.items() if value}
    result["portrait_data_uri"] = _portrait_data_uri(
        source, result.get("portrait_bbox"), result.get("portrait_side")
    )
    result["warnings"] = list(dict.fromkeys(warnings))
    result["confidence"] = _percent_confidence(result.get("confidence"))
    logger.info(
        "CNIC OCR timing phase=front outcome=success total_ms=%s normalization_ms=%s "
        "primary_ms=%s retry_ms=%s metrics=%s",
        round((time.monotonic() - started) * 1000), normalization_ms,
        primary_ms, retry_ms, metrics,
    )
    return result


def extract_cnic_back_identity(back_file, expected_front_number, model):
    """Read the back and verify it against a server-trusted front-side number."""
    started = time.monotonic()
    expected_digits = re.sub(r"\D", "", expected_front_number or "")
    if len(expected_digits) != 13:
        return _unavailable("The provisional CNIC front reading has expired. Read the front again.")
    if not getattr(settings, "OPENAI_API_KEY", ""):
        return _unavailable("CNIC OCR is unavailable because OPENAI_API_KEY is not configured.")
    if not back_file:
        return _unavailable("Choose the CNIC Back image to complete verification.")

    try:
        source, encoded, normalized_mime, metrics = _prepare_staged_cnic_image(
            back_file, "back"
        )
    except ValueError as exc:
        return _unavailable(str(exc))
    normalization_ms = round((time.monotonic() - started) * 1000)
    content = [
        {"type": "input_text", "text": CNIC_STAGED_BACK_PROMPT.strip()},
        {
            "type": "input_image",
            "image_url": f"data:{normalized_mime};base64,{encoded}",
            "detail": "high",
        },
    ]
    enhanced = _enhanced_back_image_data(
        ContentFile(source, name=getattr(back_file, "name", "cnic-back.jpg"))
    )
    if enhanced:
        content.extend([
            {
                "type": "input_text",
                "text": "The next image is an enlarged address crop from the same CNIC back.",
            },
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{enhanced}",
                "detail": "high",
            },
        ])
    primary_started = time.monotonic()
    try:
        response = _openai_client().responses.create(
            model=model,
            store=False,
            max_output_tokens=900,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pakistani_cnic_staged_back",
                    "strict": True,
                    "schema": CNIC_STAGED_BACK_SCHEMA,
                }
            },
            input=[{"role": "user", "content": content}],
        )
    except ImportError:
        return _unavailable("The openai Python package is not installed.")
    except Exception as exc:
        error_kind = _openai_error_kind(exc)
        logger.warning("CNIC staged back OCR failed error=%s", error_kind)
        return _unavailable(_staged_ocr_error_message(error_kind, "back"))
    primary_ms = round((time.monotonic() - primary_started) * 1000)

    result = _parse_json(getattr(response, "output_text", "") or "")
    result.update(engine="openai", model=model, usage=_usage(response))
    if result.get("document_side") != "back":
        return {
            **result,
            "fields": {},
            "message": "The selected image was not recognized as the CNIC back.",
        }
    back_digits = re.sub(r"\D", "", result.get("back_identity_number") or "")
    retry_ms = 0
    if len(back_digits) != 13:
        retry_started = time.monotonic()
        back_digits = _retry_staged_identity_number(source, "back", model)
        retry_ms = round((time.monotonic() - retry_started) * 1000)
    if len(back_digits) != 13:
        outcome = "back_number_unreadable"
        message = "The CNIC number on the back could not be read clearly. Upload a clearer back image."
    elif back_digits != expected_digits:
        outcome = "number_mismatch"
        message = "CNIC verification failed: the number on the back does not match the front."
    else:
        outcome = "success"
        message = ""
    if message:
        logger.info(
            "CNIC OCR timing phase=back outcome=%s total_ms=%s normalization_ms=%s "
            "primary_ms=%s retry_ms=%s metrics=%s",
            outcome, round((time.monotonic() - started) * 1000), normalization_ms,
            primary_ms, retry_ms, metrics,
        )
        return {**result, "fields": {}, "message": message}

    temporary_urdu = _text(result.get("temporary_address_urdu"))
    permanent_urdu = _text(result.get("permanent_address_urdu"))
    temporary_english = _text(result.get("temporary_address_english"))
    permanent_english = _text(result.get("permanent_address_english"))
    warnings = [str(item).strip() for item in result.get("warnings", []) if str(item).strip()]
    if not (temporary_urdu or temporary_english):
        warnings.append("Current address could not be read from the upper address block.")
    elif _confidence(result.get("temporary_address_confidence")) < 0.75:
        warnings.append("Verify the best-effort current address transcription.")
    if not (permanent_urdu or permanent_english):
        warnings.append("Permanent address could not be read from the lower address block.")
    elif _confidence(result.get("permanent_address_confidence")) < 0.75:
        warnings.append("Verify the best-effort permanent address transcription.")
    cnic = f"{expected_digits[:5]}-{expected_digits[5:12]}-{expected_digits[12]}"
    fields = {
        "cnic": cnic,
        "temporary_address": temporary_english,
        "permanent_address": permanent_english,
        "temporary_address_urdu": temporary_urdu,
        "permanent_address_urdu": permanent_urdu,
    }
    result["fields"] = {key: value for key, value in fields.items() if value}
    result["cnic_verified"] = True
    result["warnings"] = list(dict.fromkeys(warnings))
    result["confidence"] = _percent_confidence(result.get("confidence"))
    logger.info(
        "CNIC OCR timing phase=back outcome=success total_ms=%s normalization_ms=%s "
        "primary_ms=%s retry_ms=%s metrics=%s",
        round((time.monotonic() - started) * 1000), normalization_ms,
        primary_ms, retry_ms, metrics,
    )
    return result


def extract_cnic_identity(front_file, back_file, model):
    """Return review-only identity suggestions from both sides of a CNIC."""
    ocr_started = time.monotonic()
    normalization_ms = 0
    primary_ms = 0
    retry_ms = 0
    retry_used = False

    def log_timing(outcome):
        logger.info(
            "CNIC OCR timing outcome=%s model=%s total_ms=%s normalization_ms=%s "
            "primary_ms=%s retry_ms=%s retry_used=%s image_count=%s",
            outcome,
            model,
            round((time.monotonic() - ocr_started) * 1000),
            normalization_ms,
            primary_ms,
            retry_ms,
            retry_used,
            sum(item.get("type") == "input_image" for item in images),
        )

    if not getattr(settings, "OPENAI_API_KEY", ""):
        return _unavailable("CNIC OCR is unavailable because OPENAI_API_KEY is not configured.")
    if not front_file or not back_file:
        return _unavailable("Choose both CNIC Front and CNIC Back images first.")

    images = []
    front_source = b""
    back_source = b""
    auto_rotated_sides = []
    address_image_sufficient = False
    image_metrics = {}
    normalization_started = time.monotonic()
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
            source, was_auto_rotated = _auto_orient_cnic_source(source, side=label)
            if was_auto_rotated:
                auto_rotated_sides.append(label)
            if label == "front":
                front_source = source
            with Image.open(BytesIO(source)) as dimension_image:
                image_metrics[label] = (
                    dimension_image.width,
                    dimension_image.height,
                    len(source),
                )
                if label == "back":
                    address_image_sufficient = (
                        dimension_image.width >= 600
                        and dimension_image.height >= 350
                    )
            if label == "back":
                back_source = source
            normalized_file = ContentFile(
                source, name=getattr(file_field, "name", f"cnic-{label}.jpg")
            )
            encoded, normalized_mime = _normalized_image_data(normalized_file)
        except Exception:
            logger.warning("CNIC OCR image normalization failed side=%s", label)
            normalization_ms = round((time.monotonic() - normalization_started) * 1000)
            log_timing("normalization_failed")
            return _unavailable(f"CNIC {label} image could not be prepared for OCR.")
        images.append(
            {
                "type": "input_image",
                "image_url": f"data:{normalized_mime};base64,{encoded}",
                "detail": "high",
            }
        )
        if label == "back":
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

    normalization_ms = round((time.monotonic() - normalization_started) * 1000)
    primary_started = time.monotonic()
    try:
        response = _openai_client().responses.create(
            model=model,
            store=False,
            max_output_tokens=CNIC_OCR_MAX_OUTPUT_TOKENS,
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
        primary_ms = round((time.monotonic() - primary_started) * 1000)
    except ImportError:
        primary_ms = round((time.monotonic() - primary_started) * 1000)
        log_timing("package_unavailable")
        return _unavailable("The openai Python package is not installed.")
    except Exception as exc:
        primary_ms = round((time.monotonic() - primary_started) * 1000)
        error_kind = _openai_error_kind(exc)
        logger.warning("CNIC OCR failed error=%s", error_kind)
        log_timing(error_kind)
        if error_kind == "rate_limit":
            return _unavailable(
                "CNIC OCR is temporarily busy. Wait one minute, then click Read CNIC Details again."
            )
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
        log_timing("not_cnic")
        return {
            **result,
            "fields": {},
            "message": "Both selected images were not recognized as one Pakistani CNIC.",
        }

    warnings = [str(item).strip() for item in result.get("warnings", []) if str(item).strip()]
    front_source, back_source, sides_were_swapped = _ordered_cnic_sources(
        result, front_source, back_source
    )
    if sides_were_swapped:
        result["sides_were_swapped"] = True
        warnings.append(
            "The uploaded CNIC front and back were detected in reverse order and corrected."
        )
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
    rechecked_front = ""
    rechecked_back = ""
    if (
        len(front_cnic_digits) != 13
        or len(back_cnic_digits) != 13
        or front_cnic_digits != back_cnic_digits
    ):
        retry_used = True
        retry_started = time.monotonic()
        rechecked = _retry_identity_numbers(front_source, back_source, model)
        retry_ms = round((time.monotonic() - retry_started) * 1000)
        rechecked_front = re.sub(
            r"\D", "", rechecked.get("front_identity_number") or ""
        )
        rechecked_back = re.sub(
            r"\D", "", rechecked.get("back_identity_number") or ""
        )
        if (
            len(rechecked_front) == 13
            and rechecked_front == rechecked_back
        ):
            front_cnic_digits = rechecked_front
            back_cnic_digits = rechecked_back
            result["front_identity_number"] = rechecked.get("front_identity_number")
            result["back_identity_number"] = rechecked.get("back_identity_number")
            warnings.append(
                "The CNIC numbers were confirmed by a focused second reading."
            )
    if len(front_cnic_digits) != 13:
        logger.warning(
            "CNIC OCR front number unreadable model=%s front_metrics=%s "
            "back_metrics=%s initial_front_digits=%s initial_back_digits=%s "
            "retry_front_digits=%s retry_back_digits=%s",
            model,
            image_metrics.get("front"),
            image_metrics.get("back"),
            len(front_cnic_digits),
            len(back_cnic_digits),
            len(rechecked_front),
            len(rechecked_back),
        )
        log_timing("front_number_unreadable")
        return {
            **result,
            "fields": {},
            "message": "The CNIC number on the front could not be read clearly. Upload a clearer front image.",
        }
    if len(back_cnic_digits) != 13:
        log_timing("back_number_unreadable")
        return {
            **result,
            "fields": {},
            "message": "The CNIC number at the top-right of the back could not be read clearly. Upload a clearer back image.",
        }
    if front_cnic_digits != back_cnic_digits:
        log_timing("number_mismatch")
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
        "first_name": _text(result.get("name")),
        "last_name": _text(result.get("father_name")),
        "gender": result.get("gender") if result.get("gender") in {"M", "F", "O"} else None,
        "country": _text(result.get("country_of_stay")),
        "nationality": "Pakistani",
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
        result.get("portrait_side"),
    )
    result["warnings"] = list(dict.fromkeys(warnings))
    log_timing("success")
    return result


def _prepare_staged_cnic_image(file_field, side):
    mime_type = (
        getattr(file_field, "content_type", "")
        or mimetypes.guess_type(getattr(file_field, "name", "") or "")[0]
        or ""
    )
    if not mime_type.startswith("image/"):
        raise ValueError(f"CNIC {side} must be an image file.")
    try:
        file_field.open("rb")
        source = file_field.read()
        file_field.close()
        source, _was_rotated = _auto_orient_cnic_source(source, side=side)
        with Image.open(BytesIO(source)) as image:
            metrics = (image.width, image.height, len(source))
        normalized_file = ContentFile(
            source, name=getattr(file_field, "name", f"cnic-{side}.jpg")
        )
        encoded, normalized_mime = _normalized_image_data(normalized_file)
    except Exception as exc:
        logger.warning("CNIC staged OCR image preparation failed side=%s", side)
        raise ValueError(f"CNIC {side} image could not be prepared for OCR.") from exc
    return source, encoded, normalized_mime, metrics


def _retry_staged_identity_number(source, side, model):
    enhanced = (
        _enhanced_front_image_data(source)
        if side == "front"
        else _enhanced_back_identity_image_data(source)
    )
    if not enhanced:
        return ""
    try:
        response = _openai_client().responses.create(
            model=model,
            store=False,
            max_output_tokens=120,
            text={
                "format": {
                    "type": "json_schema",
                    "name": f"pakistani_cnic_staged_{side}_number",
                    "strict": True,
                    "schema": CNIC_STAGED_NUMBER_SCHEMA,
                }
            },
            input=[{
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Read the complete 13-digit Pakistani CNIC identity number "
                            f"from this {side} crop. Ignore QR data and any separate card "
                            "serial. Return null rather than guessing an unclear digit."
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{enhanced}",
                        "detail": "high",
                    },
                ],
            }],
        )
        parsed = _parse_json(getattr(response, "output_text", "") or "")
        digits = re.sub(r"\D", "", parsed.get("identity_number") or "")
        return digits if len(digits) == 13 else ""
    except Exception as exc:
        logger.warning(
            "CNIC staged number retry failed side=%s error=%s",
            side,
            _openai_error_kind(exc),
        )
        return ""


def _percent_confidence(value):
    return max(0, min(100, round(_confidence(value) * 100)))


def _valid_dob(value):
    dob = _date_or_none(value)
    if dob and dob <= date.today() and _age_on(dob, date.today()) <= 120:
        return dob
    return None


def _ordered_cnic_sources(result, first_source, second_source):
    """Return actual front/back bytes using the model's visual side classification."""
    if (
        result.get("front_image_index") == 2
        and result.get("back_image_index") == 1
    ):
        return second_source, first_source, True
    return first_source, second_source, False


def _text(value):
    value = " ".join(str(value or "").split())
    return value or None


def _confidence(value):
    try:
        return max(0.0, min(1.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _cnic_region_transition_score(image, box):
    """Measure dense dark/light transitions in a normalized image region."""
    width, height = image.size
    left = max(0, min(width - 1, round(box[0] * width)))
    top = max(0, min(height - 1, round(box[1] * height)))
    right = max(left + 1, min(width, round(box[2] * width)))
    bottom = max(top + 1, min(height, round(box[3] * height)))
    region = ImageOps.autocontrast(
        image.convert("L").crop((left, top, right, bottom)), cutoff=1
    )
    region.thumbnail((180, 180), Image.Resampling.LANCZOS)
    pixels = region.load()
    transitions = 0
    comparisons = 0
    for y in range(region.height):
        for x in range(region.width):
            current = pixels[x, y]
            if x + 1 < region.width:
                transitions += abs(current - pixels[x + 1, y]) >= 48
                comparisons += 1
            if y + 1 < region.height:
                transitions += abs(current - pixels[x, y + 1]) >= 48
                comparisons += 1
    return transitions / comparisons if comparisons else 0.0


def _cnic_back_is_upside_down(image):
    """Detect a half-turn from the contrast-normalized QR/layout regions."""
    expected = _cnic_region_transition_score(image, (0.64, 0.02, 0.98, 0.54))
    inverted = _cnic_region_transition_score(image, (0.02, 0.46, 0.36, 0.98))
    return inverted >= 0.025 and inverted > expected * 1.18


def _cnic_region_portrait_color_score(image, box):
    """Estimate portrait-like warm pixels in a normalized card region."""
    width, height = image.size
    left = max(0, min(width - 1, round(box[0] * width)))
    top = max(0, min(height - 1, round(box[1] * height)))
    right = max(left + 1, min(width, round(box[2] * width)))
    bottom = max(top + 1, min(height, round(box[3] * height)))
    region = image.convert("RGB").crop((left, top, right, bottom))
    region.thumbnail((180, 180), Image.Resampling.LANCZOS)
    portrait_pixels = 0
    pixels = 0
    for red, green, blue in region.getdata():
        if (
            45 < red < 245
            and 30 < green < 225
            and 20 < blue < 215
            and red > green * 1.06
            and red > blue * 1.08
            and abs(green - blue) < 75
        ):
            portrait_pixels += 1
        pixels += 1
    return portrait_pixels / pixels if pixels else 0.0


def _cnic_region_portrait_detail_score(image, box):
    """Measure dark facial detail and contrast without mistaking the gold chip."""
    width, height = image.size
    left = max(0, min(width - 1, round(box[0] * width)))
    top = max(0, min(height - 1, round(box[1] * height)))
    right = max(left + 1, min(width, round(box[2] * width)))
    bottom = max(top + 1, min(height, round(box[3] * height)))
    region = image.convert("L").crop((left, top, right, bottom))
    region.thumbnail((180, 180), Image.Resampling.LANCZOS)
    values = list(region.getdata())
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    dark_ratio = sum(value < 105 for value in values) / len(values)
    contrast = variance ** 0.5 / 255
    return dark_ratio + max(0.0, contrast - 0.08) * 0.45


def _cnic_portrait_fallback_crop(image):
    """Choose the portrait side for current and older Pakistani CNIC layouts."""
    left_region = (0.02, 0.12, 0.34, 0.84)
    right_region = (0.66, 0.12, 0.99, 0.84)
    left_score = _cnic_region_portrait_detail_score(
        image, left_region
    ) + _cnic_region_portrait_color_score(image, left_region) * 0.15
    right_score = _cnic_region_portrait_detail_score(
        image, right_region
    ) + _cnic_region_portrait_color_score(image, right_region) * 0.15
    # Older fronts also have a detailed coat-of-arms/security pattern on the
    # right, so the left portrait need only be clearly (not overwhelmingly)
    # stronger. Current cards still produce a much stronger right-side score.
    if left_score >= 0.055 and left_score > right_score * 1.10:
        return CNIC_LEFT_PORTRAIT_CROP
    return CNIC_PORTRAIT_CROP


def _cnic_gold_chip_score(image):
    """Estimate whether the current-layout gold chip occupies the left card area."""
    width, height = image.size
    region = image.convert("RGB").crop(
        (round(width * 0.06), round(height * 0.28), round(width * 0.30), round(height * 0.67))
    )
    region.thumbnail((100, 100), Image.Resampling.LANCZOS)
    pixels = list(region.getdata())
    if not pixels:
        return 0.0
    gold_pixels = sum(
        red > 60
        and red > green * 1.08
        and green > blue * 1.05
        and max(red, green, blue) - min(red, green, blue) > 20
        for red, green, blue in pixels
    )
    return gold_pixels / len(pixels)


def _cnic_portrait_crop(image, portrait_side=None):
    """Choose portrait side using card layout evidence, then the OCR hint."""
    local_crop = _cnic_portrait_fallback_crop(image)
    chip_score = _cnic_gold_chip_score(image)
    if local_crop == CNIC_LEFT_PORTRAIT_CROP and chip_score < 0.05:
        return CNIC_LEFT_PORTRAIT_CROP
    if chip_score >= 0.055:
        return CNIC_PORTRAIT_CROP
    if portrait_side == "left":
        return CNIC_LEFT_PORTRAIT_CROP
    if portrait_side == "right":
        return CNIC_PORTRAIT_CROP
    return local_crop


def _cnic_portrait_bbox_is_safe(box, expected_crop=None):
    """Accept OCR portrait boxes only near a supported left/right card layout."""
    x, y, box_width, box_height = box
    if not (
        0 <= x < 1
        and 0 <= y < 1
        and 0.08 <= box_width <= 0.55
        and 0.15 <= box_height <= 0.95
        and x + box_width <= 1.02
        and y + box_height <= 1.02
    ):
        return False
    anchors = (expected_crop,) if expected_crop else CNIC_PORTRAIT_CROPS
    return any(
        abs(x - anchor_x) <= 0.10
        and abs(y - anchor_y) <= 0.15
        and abs(box_width - anchor_width) <= 0.12
        and abs(box_height - anchor_height) <= 0.20
        for anchor_x, anchor_y, anchor_width, anchor_height in anchors
    )


def _auto_orient_cnic_source(source, side=None):
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
            exif_orientation = opened.getexif().get(274, 1)
            image = ImageOps.exif_transpose(opened)
            image.load()
            was_rotated = exif_orientation not in (None, 1)
            if image.height > image.width:
                image = image.rotate(90, expand=True)
                was_rotated = True
            if side == "back" and _cnic_back_is_upside_down(image):
                image = image.rotate(180, expand=True)
                was_rotated = True
            if not was_rotated:
                return source, False
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
        return output.getvalue(), was_rotated
    except Exception:
        logger.warning("CNIC OCR auto-orientation was skipped for one image.")
        return source, False


def oriented_cnic_content_file(file_field, *, side, filename=None):
    """Return an upload whose pixels are stored in the detected upright orientation."""
    if not file_field:
        return None
    opened_here = bool(getattr(file_field, "closed", True))
    try:
        if opened_here:
            file_field.open("rb")
        else:
            file_field.seek(0)
        source = file_field.read()
        if opened_here:
            file_field.close()
        else:
            file_field.seek(0)
    except Exception:
        logger.warning("CNIC upload could not be read for orientation correction.")
        return file_field

    oriented, was_rotated = _auto_orient_cnic_source(source, side=side)
    if not was_rotated:
        return file_field
    original_name = filename or getattr(file_field, "name", "") or f"cnic-{side}.jpg"
    stem = original_name.rsplit(".", 1)[0]
    return ContentFile(oriented, name=f"{stem}-oriented.jpg")


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
                    max(0, int(width * 0.12)),
                    max(0, int(height * 0.48)),
                    max(1, int(width * 0.88)),
                    max(1, int(height * 0.98)),
                )
            )
            target_width = min(
                CNIC_ENHANCED_MAX_WIDTH, max(2200, image.width * 3)
            )
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


def _retry_identity_numbers(front_source, back_source, model):
    """Independently reread both number crops after an unclear or mismatched pass."""
    enhanced_front = _enhanced_front_image_data(front_source)
    enhanced_back = _enhanced_back_identity_image_data(back_source)
    if not enhanced_front or not enhanced_back:
        return {}
    try:
        response = _openai_client().responses.create(
            model=model,
            store=False,
            max_output_tokens=200,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "pakistani_cnic_number_recheck",
                    "strict": True,
                    "schema": CNIC_NUMBER_RECHECK_SCHEMA,
                }
            },
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Read the complete Pakistani CNIC identity number from "
                                "each supplied crop independently. The first crop is the "
                                "front Identity Number and the second crop is the back "
                                "identity-number area. On an older back the number may be "
                                "left below the portrait; on a newer back it may be top-right. "
                                "Ignore QR data and any separate 12-digit serial. Preserve all 13 digits, "
                                "including the final check digit. Never copy a value from "
                                "one crop to the other; return null when a crop is unclear."
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{enhanced_front}",
                            "detail": "high",
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{enhanced_back}",
                            "detail": "high",
                        },
                    ],
                }
            ],
        )
        return _parse_json(getattr(response, "output_text", "") or "")
    except Exception as exc:
        logger.warning(
            "CNIC number recheck failed error=%s", _openai_error_kind(exc)
        )
        return {}


def _enhanced_back_identity_image_data(source):
    """Return the enlarged upper back, covering both old and new number layouts."""
    if not source:
        return None
    try:
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened).convert("L")
            width, height = image.size
            image = image.crop(
                (
                    0,
                    0,
                    width,
                    max(1, int(height * 0.62)),
                )
            )
            target_width = min(
                CNIC_ENHANCED_MAX_WIDTH, max(2200, image.width * 3)
            )
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
            target_width = min(
                CNIC_ENHANCED_MAX_WIDTH, max(2200, image.width * 3)
            )
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


def _portrait_data_uri(source, bbox=None, portrait_side=None):
    """Return a compact JPEG portrait crop from the CNIC front."""
    if not source:
        return ""
    try:
        source, _was_auto_rotated = _auto_orient_cnic_source(source, side="front")
        with Image.open(BytesIO(source)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            width, height = image.size
            fallback_crop = _cnic_portrait_crop(image, portrait_side)
            values = bbox if isinstance(bbox, dict) else {}
            try:
                x = float(values.get("x"))
                y = float(values.get("y"))
                box_width = float(values.get("width"))
                box_height = float(values.get("height"))
            except (TypeError, ValueError):
                x, y, box_width, box_height = fallback_crop
            if not _cnic_portrait_bbox_is_safe(
                (x, y, box_width, box_height), expected_crop=fallback_crop
            ):
                x, y, box_width, box_height = fallback_crop
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


def _staged_ocr_error_message(error_kind, side):
    if error_kind == "insufficient_quota":
        return (
            "CNIC OCR is unavailable because the OpenAI credit or quota is exhausted. "
            "Continue with manual entry or ask an administrator to check API billing."
        )
    if error_kind == "rate_limit":
        return "CNIC OCR is temporarily rate limited. Wait one minute or continue with manual entry."
    if error_kind == "timeout":
        return "CNIC OCR timed out. Try again later or continue with manual entry."
    if error_kind == "connection_error":
        return "CNIC OCR could not reach OpenAI. Check the connection or continue with manual entry."
    return f"The CNIC {side} could not be read. Try a clearer image or continue with manual entry."


def _unavailable(message):
    return {
        "engine": "unavailable",
        "fields": {},
        "confidence": 0,
        "warnings": [],
        "message": message,
    }
