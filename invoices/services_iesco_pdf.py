from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.utils import timezone
from pypdf import PdfReader
from pypdf.errors import PdfReadError


MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
MONTH_RE = re.compile(rf"\b({MONTHS})\s+(\d{{2}}|\d{{4}})\b", re.IGNORECASE)
DATE_RE = re.compile(rf"\b(\d{{1,2}})\s+({MONTHS})\s+(\d{{2}}|\d{{4}})\b", re.IGNORECASE)
REFERENCE_RE = re.compile(r"(?<!\d)(?:\d[\s-]*){14}(?!\d)")
AMOUNT_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:/\d+)?")


def _clean_text(value: str) -> str:
    value = value.replace("\x00", " ").replace("\xa0", " ")
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in value.splitlines()
        if line.strip()
    )


def _reference_candidates(text: str) -> list[str]:
    lines = text.splitlines()
    candidates = []
    for index, line in enumerate(lines):
        if "REFERENCE NO" not in line.upper():
            continue
        section = " ".join(lines[index : index + 4])
        candidates.extend(
            re.sub(r"\D", "", match.group(0))
            for match in REFERENCE_RE.finditer(section)
        )
    candidates.extend(
        match.group(1)
        for match in re.finditer(r"refno=(\d{14})", text, re.IGNORECASE)
    )
    candidates.extend(
        re.sub(r"\D", "", match.group(0))
        for match in REFERENCE_RE.finditer(text)
    )
    return list(dict.fromkeys(value for value in candidates if len(value) == 14))


def _canonical_reference(text: str, known_references) -> str:
    known = {str(value) for value in known_references if re.fullmatch(r"\d{14}", str(value))}
    for candidate in _reference_candidates(text):
        if candidate in known:
            return candidate
        suffix_matches = [value for value in known if value[-12:] == candidate[-12:]]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
    raise ValidationError(
        "The PDF reference number does not match a visible IESCO meter. "
        "Assign the meter first, then upload the PDF again."
    )


def _bill_month_and_dates(text: str):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "BILL MONTH" not in line.upper():
            continue
        section = " ".join(lines[index : index + 10])
        months = [
            (month.upper(), year[-2:])
            for month, year in MONTH_RE.findall(section)
        ]
        if not months:
            continue
        counts = Counter(months)
        month, year = max(months, key=lambda value: counts[value])
        bill_month = f"{month} {year}"
        matching_dates = []
        for day, date_month, date_year in DATE_RE.findall(section):
            if (date_month.upper(), date_year[-2:]) == (month, year):
                matching_dates.append(f"{int(day):02d} {month} {year}")
        reading_date = matching_dates[0] if len(matching_dates) > 0 else None
        issue_date = matching_dates[1] if len(matching_dates) > 1 else None
        due_date = matching_dates[2] if len(matching_dates) > 2 else None
        return bill_month, reading_date, issue_date, due_date
    raise ValidationError("The bill month could not be read from this PDF.")


def _line_value(text: str, label: str):
    for line in text.splitlines():
        position = line.upper().find(label.upper())
        if position < 0:
            continue
        tail = line[position + len(label) :]
        # The newer PITC PDF inserts Urdu text between the English label and
        # value. Reject another English column heading, but allow that Urdu.
        label_tail = re.sub(r"\bCR\b", "", tail, flags=re.IGNORECASE)
        if re.search(r"[A-Za-z]", label_tail):
            continue
        matches = AMOUNT_RE.findall(tail)
        if not matches:
            continue
        raw = matches[-1].split("/", 1)[0].replace(",", "")
        try:
            value = Decimal(raw)
        except (InvalidOperation, ValueError):
            continue
        if "CR" in tail.upper() and value > 0:
            value = -value
        return value
    return None


def _format_decimal(value):
    if value is None:
        return None
    if value == value.to_integral_value():
        return str(int(value))
    return format(value.normalize(), "f")


def _consumer_id(text: str):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "CONSUMER ID" not in line.upper():
            continue
        section = " ".join(lines[index : index + 3])
        match = re.search(r"(?<!\d)(\d{10})(?!\d)", section)
        if match:
            return match.group(1)
    return None


def _meter_readings(layout_text: str):
    number = r"-?(?:\d[\d,]*(?:\.\d*)?|\.\d+)"
    lines = layout_text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "METER NO" in line
            and "PREVIOUS" in line
            and "PRESENT" in line
            and "UNITS" in line
        ),
        None,
    )
    if header_index is None:
        return None, []

    header = lines[header_index]
    previous_column = header.find("PREVIOUS")
    present_column = header.find("PRESENT", previous_column + 1)
    units_column = header.find("UNITS", present_column + 1)
    multiplier_column = header.find("MF", present_column + 1)
    present_end = (
        multiplier_column
        if present_column < multiplier_column < units_column
        else units_column
    )
    section = []
    for line in lines[header_index + 1 :]:
        if "BILL CHARGES" in line or "IESCO CHARGES" in line:
            break
        section.append(line)

    def column_values(start, end):
        values = []
        for line in section:
            match = re.search(number, line[start:end])
            if match:
                values.append(match.group(0).replace(",", ""))
        return values[:4]

    previous_values = column_values(previous_column, present_column)
    present_values = column_values(present_column, present_end)
    unit_values = column_values(units_column, units_column + 24)
    count = min(len(previous_values), len(present_values), len(unit_values), 4)
    if not count:
        return None, []

    direction_labels = [
        "import" if value.lower() == "imp" else "export"
        for value in re.findall(r"\b(IMP|EXP)\s+1", "\n".join(section), re.IGNORECASE)
    ]
    directions = (
        direction_labels[:count]
        if len(direction_labels) >= count
        else ["import" if index < 2 else "export" for index in range(count)]
    )

    meter_match = re.search(r"(?<!\d)(013\d{11})(?!\d)", "\n".join(section))
    meter_no = meter_match.group(1) if meter_match else None
    counters = {"import": 0, "export": 0}
    readings = []
    for index in range(count):
        direction = directions[index]
        period = "off_peak" if counters[direction] == 0 else "peak"
        counters[direction] += 1
        readings.append(
            {
                "direction": direction,
                "period": period,
                "meter_no": meter_no,
                "multiplier": "1",
                "previous": previous_values[index],
                "present": present_values[index],
                "units": unit_values[index],
            }
        )
    return "3-P" if len(readings) > 1 else "S-P", readings


def extract_iesco_pdf_payload(upload, known_references) -> dict:
    """Read identity and available bill figures from an original PITC PDF."""
    try:
        upload.seek(0)
        reader = PdfReader(upload)
        if reader.is_encrypted:
            raise ValidationError("Password-protected PDFs are not supported.")
        if not reader.pages or len(reader.pages) > 10:
            raise ValidationError("The file must contain an IESCO bill of 1 to 10 pages.")
        text = _clean_text("\n".join(page.extract_text() or "" for page in reader.pages))
        layout_text = reader.pages[0].extract_text(extraction_mode="layout") or ""
    except ValidationError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise ValidationError("The selected file is not a readable PDF bill.") from exc
    finally:
        upload.seek(0)

    if len(text) < 100 or "BILL MONTH" not in text.upper():
        raise ValidationError(
            "No readable IESCO bill text was found. Upload the original PITC PDF, not a scanned image."
        )

    reference_no = _canonical_reference(text, known_references)
    bill_month, reading_date, issue_date, due_date = _bill_month_and_dates(text)
    current_bill = _line_value(text, "CURRENT BILL")
    arrears = _line_value(text, "ARREARS")
    if arrears is None:
        arrears = _line_value(text, "ARREAR/AGE")
    grand_total = _line_value(text, "GRAND TOTAL")
    if grand_total is None:
        grand_total = _line_value(text, "PAYABLE WITHIN DUE DATE")
    units = _line_value(text, "UNITS CONSUMED")
    meter_type, meter_readings = _meter_readings(layout_text)
    if units is None and meter_readings:
        units = sum(
            (
                Decimal(row["units"])
                for row in meter_readings
                if row["direction"] == "import"
            ),
            Decimal("0"),
        )
    if grand_total is None and current_bill is not None and arrears is not None:
        grand_total = current_bill + arrears
    if (
        grand_total is not None
        and current_bill is not None
        and grand_total < current_bill
        and (arrears is None or arrears >= 0)
    ):
        arrears = grand_total - current_bill

    return {
        "reference_no": reference_no,
        "fetched_at": timezone.now().isoformat(),
        "consumer_id": _consumer_id(text),
        "meter_type": meter_type,
        "bill_month": bill_month,
        "reading_date": reading_date,
        "issue_date": issue_date,
        "due_date": due_date,
        "units": _format_decimal(units),
        "current_bill": _format_decimal(current_bill),
        "arrears": _format_decimal(arrears),
        "grand_total": _format_decimal(grand_total),
        "meter_readings": meter_readings,
        "bill_history": [],
        "current_month_paid": None,
    }
