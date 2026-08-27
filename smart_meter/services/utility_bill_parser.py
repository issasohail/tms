from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import fitz


class UtilityBillParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedUtilityBill:
    data: dict
    warnings: list[str]
    raw_text: str


NUMBER = r"(?:-?\d[\d,]*(?:\.\d+)?(?:\s*CR)?)"
DATE_FORMATS = ("%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")


def _decimal(value):
    if value is None:
        return None
    cleaned = re.sub(r"\s+", "", value).replace(",", "")
    credit = cleaned.upper().endswith("CR")
    cleaned = re.sub(r"CR$", "", cleaned, flags=re.I)
    try:
        result = Decimal(cleaned)
    except InvalidOperation as exc:
        raise UtilityBillParseError(f"Invalid numeric value: {value}") from exc
    return -abs(result) if credit else result


def _find(text, label, pattern, flags=re.I):
    match = re.search(rf"{label}\s*[:\-]?\s*({pattern})", text, flags)
    return match.group(1).strip() if match else None


def _parse_date(value):
    if not value:
        return None
    value = re.sub(r"\s+", "", value)
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    raise UtilityBillParseError(f"Unrecognized date: {value}")


def _register_values(text, labels):
    label_pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(
        rf"(?:{label_pattern})[^\d\-]*({NUMBER})\s+({NUMBER})\s+({NUMBER})(?:\s+{NUMBER})?",
        text,
        re.I,
    )
    if not match:
        return None
    # The optional fourth number is MDI (kW) and is deliberately discarded.
    return tuple(_decimal(match.group(index)) for index in (1, 2, 3))


def parse_utility_bill(upload) -> ParsedUtilityBill:
    content = upload.read()
    if hasattr(upload, "seek"):
        upload.seek(0)
    if not content.startswith(b"%PDF-"):
        raise UtilityBillParseError("The uploaded content is not a PDF file.")
    try:
        document = fitz.open(stream=content, filetype="pdf")
        if document.page_count < 1:
            raise UtilityBillParseError("The PDF has no pages.")
        text = "\n".join(page.get_text("text") for page in document)
    except UtilityBillParseError:
        raise
    except Exception as exc:
        raise UtilityBillParseError("The PDF is malformed or cannot be read.") from exc
    finally:
        if "document" in locals():
            document.close()
    if not text.strip():
        raise UtilityBillParseError("The PDF has no readable text layer; enter the bill manually.")

    normalized = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    compact = re.sub(r"\s+", " ", normalized)
    consumer_id = _find(compact, r"CONSUMER\s*ID", r"[A-Z0-9\-]+")
    if not consumer_id:
        raise UtilityBillParseError("Consumer ID could not be identified; enter the bill manually.")

    data = {
        "consumer_id": consumer_id,
        "reference_no": _find(compact, r"REFERENCE\s*(?:NO|NUMBER)", r"[A-Z0-9\-]+"),
        "bill_month": _find(compact, r"BILL\s*MONTH", r"[A-Z]{3,9}[\s\-]+\d{2,4}"),
        "reading_date": _parse_date(_find(compact, r"READING\s*DATE", r"\d{1,2}[-/.][A-Z0-9]{1,9}[-/.]\d{2,4}")),
        "issue_date": _parse_date(_find(compact, r"ISSUE\s*DATE", r"\d{1,2}[-/.][A-Z0-9]{1,9}[-/.]\d{2,4}")),
        "due_date": _parse_date(_find(compact, r"DUE\s*DATE", r"\d{1,2}[-/.][A-Z0-9]{1,9}[-/.]\d{2,4}")),
        "dg_capacity_kw": _decimal(_find(compact, r"DG\s*CAPACITY(?:\s*\(KW\))?", NUMBER)),
        "total_electricity_charges": _decimal(_find(compact, r"TOTAL\s+ELECTRICITY\s+CHARGES", NUMBER)),
        "taxes": _decimal(_find(compact, r"TAXES", NUMBER)),
        "current_bill": _decimal(_find(compact, r"CURRENT\s+BILL", NUMBER)),
        "arrears": _decimal(_find(compact, r"ARREARS", NUMBER)),
        "total_fpa": _decimal(_find(compact, r"TOTAL\s+FPA", NUMBER)),
        "grand_total": _decimal(_find(compact, r"GRAND\s+TOTAL", NUMBER)),
    }
    register_labels = {
        "import_off_peak": ("IMPORT OFF PEAK", "IMPORT OFF-PEAK", "IMPORT NORMAL"),
        "import_peak": ("IMPORT PEAK",),
        "export_off_peak": ("EXPORT OFF PEAK", "EXPORT OFF-PEAK", "EXPORT NORMAL"),
        "export_peak": ("EXPORT PEAK",),
    }
    warnings = []
    for prefix, labels in register_labels.items():
        values = _register_values(normalized, labels)
        if values:
            data[f"{prefix}_previous"], data[f"{prefix}_current"], data[f"{prefix}_kwh"] = values
            calculated = values[1] - values[0]
            if abs(calculated - values[2]) > Decimal("0.01"):
                warnings.append(
                    f"{labels[0]} units do not match present minus previous; review both bill tables manually."
                )
        else:
            warnings.append(f"{labels[0]} register row was not parsed; enter it manually.")

    for required, label in (
        ("bill_month", "Bill Month"),
        ("current_bill", "Current Bill"),
        ("grand_total", "Grand Total"),
    ):
        if data.get(required) is None:
            warnings.append(f"{label} was not parsed; enter it manually before confirmation.")
    return ParsedUtilityBill(data=data, warnings=warnings, raw_text=text)
