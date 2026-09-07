#!/usr/bin/env python3
"""Fetch an IESCO duplicate bill locally and push it to the TMS ingest API.

PITC is scraped because it does not expose an official API. Run this script
from a Pakistani network (for example, with Windows Task Scheduler).

Required environment variables:
    TMS_IESCO_INGEST_URL
    TMS_IESCO_API_KEY

Usage:
    python -m invoices.iesco_bill_fetch 17146151548911
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("iesco_bill")

BILL_URL = "https://bill.pitc.com.pk/gbill.aspx"
TIMEOUT = 20
HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "content-type": "application/x-www-form-urlencoded",
    "origin": "https://iesco.org.pk",
    "referer": "https://iesco.org.pk/",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


@dataclass
class BillResult:
    reference_no: str
    fetched_at: str
    consumer_id: str | None = None
    consumer_name: str | None = None
    address: str | None = None
    tariff_category: str | None = None
    units: str | None = None
    bill_month: str | None = None
    reading_date: str | None = None
    issue_date: str | None = None
    due_date: str | None = None
    grand_total: str | None = None
    bill_history: list[dict] | None = None
    current_month_paid: bool | None = None
    raw_found: bool = False


def validate_reference_no(reference_no: str) -> str:
    reference_no = str(reference_no or "").strip()
    if not re.fullmatch(r"\d{14}", reference_no):
        raise ValueError("IESCO reference number must contain exactly 14 digits")
    return reference_no


def fetch_raw_html(reference_no: str) -> str:
    reference_no = validate_reference_no(reference_no)
    response = requests.post(
        BILL_URL,
        params={"refno": reference_no},
        data={"refno": reference_no},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.text


def dump_html(reference_no: str, path: str = "iesco_raw.html") -> None:
    html = fetch_raw_html(reference_no)
    with open(path, "w", encoding="utf-8") as output:
        output.write(html)
    log.info("Saved raw HTML to %s (%d bytes)", path, len(html))


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def _val_after_label(soup: BeautifulSoup, label_text: str) -> str | None:
    label = soup.find(
        "span",
        class_="en-lbl",
        string=lambda value: value and value.strip() == label_text,
    )
    if not label:
        return None
    value = label.find_next("div", class_=lambda classes: classes and "val-space" in classes)
    return _clean(value.get_text()) if value else None


def _amount(value: str | None) -> Decimal | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_bill(html: str, reference_no: str) -> BillResult:
    reference_no = validate_reference_no(reference_no)
    soup = BeautifulSoup(html, "html.parser")
    result = BillResult(
        reference_no=reference_no,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )

    result.consumer_id = _val_after_label(soup, "CONSUMER ID")
    result.address = _val_after_label(soup, "NAME & ADDRESS")
    result.tariff_category = _val_after_label(soup, "TARIFF CATEGORY")
    result.units = _val_after_label(soup, "UNITS")
    if result.address:
        result.consumer_name = result.address.split(",", 1)[0].strip()

    grand_label = soup.find(
        "span",
        class_="charges-bd-en",
        string=lambda value: value and value.strip() == "Grand Total",
    )
    if grand_label:
        value = grand_label.find_next("span", class_="charges-bd-val")
        result.grand_total = _clean(value.get_text()) if value else None

    month_label = soup.find(
        "span",
        class_="right-panel-en",
        string=lambda value: value and "BILL MONTH" in value,
    )
    if month_label:
        value = month_label.find_next(
            "div", class_=lambda classes: classes and "right-main-val" in classes
        )
        result.bill_month = _clean(value.get_text()) if value else None

    for label_text, attribute in (
        ("READING DATE", "reading_date"),
        ("ISSUE DATE", "issue_date"),
    ):
        label = soup.find("span", string=lambda value: value and label_text in value)
        if label:
            value = label.find_next("span", class_="right-panel-date-val")
            setattr(result, attribute, _clean(value.get_text()) if value else None)

    due_label = soup.find(
        "span", class_=lambda classes: classes and "right-panel-en--due" in classes
    )
    if due_label:
        value = due_label.find_next(
            "div", class_=lambda classes: classes and "right-main-val--due" in classes
        )
        result.due_date = _clean(value.get_text()) if value else None

    history = []
    for row in soup.find_all("div", class_="history-row"):
        cells = row.find_all("div", class_="history-cell")
        if len(cells) != 5:
            continue
        bill_amount = _clean(cells[3].get_text())
        payment_amount = _clean(cells[4].get_text())
        bill_decimal = _amount(bill_amount)
        payment_decimal = _amount(payment_amount)
        paid = None
        if bill_decimal is not None and payment_decimal is not None:
            paid = bill_decimal > 0 and payment_decimal >= bill_decimal
        history.append(
            {
                "month": _clean(cells[0].get_text()),
                "units": _clean(cells[2].get_text()),
                "bill": bill_amount,
                "payment": payment_amount,
                "paid": paid,
            }
        )

    result.bill_history = history or None
    if history:
        # PITC history excludes the bill currently displayed. This is therefore
        # the paid state of the latest completed history month.
        result.current_month_paid = history[-1]["paid"]

    result.raw_found = bool(result.grand_total and result.due_date and result.bill_month)
    if not result.raw_found:
        log.warning("Required bill fields are empty; PITC markup may have changed")
    return result


def get_bill(reference_no: str) -> BillResult:
    return parse_bill(fetch_raw_html(reference_no), reference_no)


def push_to_server(bill: BillResult) -> None:
    ingest_url = os.environ.get("TMS_IESCO_INGEST_URL", "").strip()
    api_key = os.environ.get("TMS_IESCO_API_KEY", "").strip()
    if not ingest_url or not api_key:
        raise RuntimeError(
            "Set TMS_IESCO_INGEST_URL and TMS_IESCO_API_KEY before pushing"
        )
    response = requests.post(
        ingest_url,
        json=asdict(bill),
        headers={"X-API-Key": api_key},
        timeout=15,
    )
    response.raise_for_status()
    log.info("Pushed bill to TMS: %s", response.json())


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print("Usage: python -m invoices.iesco_bill_fetch <14-digit-reference-number>")
        return 2
    try:
        bill = get_bill(argv[0])
        print(json.dumps(asdict(bill), indent=2))
        if not bill.raw_found:
            log.error("Not pushing because required PITC fields were not parsed")
            return 1
        push_to_server(bill)
        return 0
    except (ValueError, RuntimeError, requests.RequestException) as exc:
        log.error("IESCO fetch failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
