import base64
import json
import logging
import re
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from pypdf import PdfReader


logger = logging.getLogger(__name__)

NOTES_LABELS = (
    "notes",
    "note",
    "property description",
    "description of property",
    "property details",
)
ESTAMP_MARKERS = ("e-stamp", "e stamp", "stamp paper", "stamp duty")


def inspect_estamp_pdf(file_field, *, ai_config=None):
    payload = _read_file(file_field)
    if not payload.startswith(b"%PDF"):
        raise ValidationError("The received document is not a valid PDF.")

    try:
        reader = PdfReader(BytesIO(payload), strict=False)
    except Exception as exc:
        raise ValidationError("The E-Stamp PDF is damaged or could not be opened.") from exc
    if reader.is_encrypted:
        raise ValidationError(
            "This E-Stamp PDF is password protected. Please send an unlocked PDF."
        )
    if not reader.pages:
        raise ValidationError("The E-Stamp PDF does not contain any pages.")

    try:
        extracted = "\n".join(
            (page.extract_text() or "").strip() for page in reader.pages[:4]
        ).strip()
    except Exception as exc:
        raise ValidationError(
            "The E-Stamp PDF text could not be read. Please resend a valid PDF."
        ) from exc
    notes_text, notes_found = _notes_section(extracted)
    source = "embedded_pdf_text" if extracted else "none"

    if (not notes_found or len(_compact(notes_text)) < 20) and ai_config and (
        ai_config.ocr_provider == "openai"
        and ai_config.openai_api_key_configured
    ):
        ocr_text = _ocr_estamp_notes(payload, ai_config.model)
        if ocr_text:
            notes_text = ocr_text
            source = "openai_scanned_pdf_ocr"

    searchable_text = notes_text or extracted
    return {
        "page_count": len(reader.pages),
        "notes_text": searchable_text[:4000],
        "source": source,
        "is_estamp": _looks_like_estamp(extracted or searchable_text),
    }


def match_properties(notes_text, properties):
    normalized_text = _normalize(notes_text)
    compact_text = normalized_text.replace(" ", "")
    matches = []
    for property_obj in properties:
        score = 0
        reasons = []
        property_name = _normalize(property_obj.property_name)
        if len(property_name.replace(" ", "")) >= 3 and (
            property_name in normalized_text
            or property_name.replace(" ", "") in compact_text
        ):
            score = max(score, 100)
            reasons.append("property name")

        address = _normalize(getattr(property_obj, "property_address1", ""))
        if len(address.replace(" ", "")) >= 6 and (
            address in normalized_text or address.replace(" ", "") in compact_text
        ):
            score = max(score, 95)
            reasons.append("address")

        house_no = _normalize(getattr(property_obj, "house_no", ""))
        street_no = _normalize(getattr(property_obj, "street_no", ""))
        if house_no and _labelled_value_present(normalized_text, house_no, ("house", "plot")):
            score = max(score, 82)
            reasons.append("house number")
        if (
            street_no
            and _labelled_value_present(normalized_text, street_no, ("street", "road"))
            and score >= 82
        ):
            score = max(score, 92)
            reasons.append("street number")

        if score:
            matches.append(
                {
                    "property": property_obj,
                    "score": score,
                    "reason": ", ".join(reasons),
                }
            )
    return sorted(
        matches,
        key=lambda item: (-item["score"], item["property"].property_name.lower()),
    )


def match_unit(notes_text, units):
    normalized_text = _normalize(notes_text)
    matches = []
    for unit in units:
        value = _normalize(unit.unit_number)
        if not value:
            continue
        labelled = _labelled_value_present(
            normalized_text, value, ("unit", "flat", "room", "shop", "office")
        )
        exact_long_value = len(value.replace(" ", "")) >= 3 and re.search(
            rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])",
            normalized_text,
        )
        if labelled or exact_long_value:
            matches.append(unit)
    return matches


def _read_file(file_field):
    try:
        file_field.open("rb")
        try:
            payload = file_field.read()
        finally:
            file_field.close()
    except (OSError, ValueError) as exc:
        raise ValidationError(
            "The E-Stamp PDF could not be downloaded from WhatsApp. Please resend it."
        ) from exc
    if not payload:
        raise ValidationError("The received E-Stamp PDF is empty.")
    return payload


def _notes_section(text):
    if not text:
        return "", False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(re.search(rf"\b{re.escape(label)}\b", lowered) for label in NOTES_LABELS):
            return "\n".join(lines[index : index + 18])[:4000], True
    return text[:4000], False


def _looks_like_estamp(text):
    lowered = (text or "").lower()
    return any(marker in lowered for marker in ESTAMP_MARKERS)


def _compact(value):
    return re.sub(r"\s+", "", value or "")


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _labelled_value_present(text, value, labels):
    flexible_value = r"\s*[-/#]?\s*".join(
        re.escape(part) for part in value.split()
    )
    label_pattern = "|".join(re.escape(label) for label in labels)
    return bool(
        re.search(
            rf"\b(?:{label_pattern})\b\s*[:#-]?\s*{flexible_value}(?![a-z0-9])",
            text,
        )
    )


def _ocr_estamp_notes(payload, model):
    try:
        import fitz
        from whatsapp.services.openai_ocr import _openai_client

        document = fitz.open(stream=payload, filetype="pdf")
        try:
            images = []
            for page_number in range(min(2, document.page_count)):
                page = document.load_page(page_number)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
                encoded = base64.b64encode(pixmap.tobytes("jpeg")).decode("ascii")
                images.append(
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{encoded}",
                        "detail": "high",
                    }
                )
        finally:
            document.close()
        if not images:
            return ""

        schema = {
            "type": "object",
            "properties": {
                "notes_text": {"type": "string"},
            },
            "required": ["notes_text"],
            "additionalProperties": False,
        }
        response = _openai_client().responses.create(
            model=model,
            max_output_tokens=min(
                800,
                max(
                    200,
                    int(
                        getattr(
                            settings,
                            "WHATSAPP_AI_OCR_MAX_OUTPUT_TOKENS",
                            300,
                        )
                    ),
                ),
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "estamp_notes",
                    "strict": True,
                    "schema": schema,
                }
            },
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Read only the Notes, Property Description, or "
                                "Property Details section of this E-Stamp paper. "
                                "Transcribe property name, address, house/plot, "
                                "street and unit/flat/room exactly as visible. "
                                "Do not infer missing values."
                            ),
                        },
                        *images,
                    ],
                }
            ],
        )
        parsed = json.loads((getattr(response, "output_text", "") or "").strip())
        return str(parsed.get("notes_text") or "").strip()[:4000]
    except Exception:
        logger.exception("Scanned E-Stamp Notes OCR failed.")
        return ""
