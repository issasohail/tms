#!/usr/bin/env python3
"""
Fetch and parse an IESCO duplicate bill from the PITC portal.

Unofficial: replicates the exact request iesco.org.pk's frontend makes to
bill.pitc.com.pk. No auth, no official API — this is scraping a public
government portal. Parser is matched to the live markup as of Sep 2026;
PITC can change this without notice.

NETWORK NOTE: bill.pitc.com.pk was unreachable (connection timeout) when
tested from a Contabo server in France. It works fine from a Pakistani IP.
If you run this from a non-Pakistani server, expect it to fail to connect —
that's a network/geo issue, not a bug here. Run from a Pakistan-based
machine/VPS, or route through a Pakistani proxy.

Usage:
    python iesco_bill_fetch.py 17146151548911
"""

import sys
import re
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("iesco_bill")

BILL_URL = "https://bill.pitc.com.pk/gbill.aspx"

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
TIMEOUT = 20


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
    raw_found: bool = False


def fetch_raw_html(reference_no: str) -> str:
    resp = requests.post(
        BILL_URL,
        params={"refno": reference_no},
        data={"refno": reference_no},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.text


def dump_html(reference_no: str, path: str = "iesco_raw.html") -> None:
    html = fetch_raw_html(reference_no)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("Saved raw HTML to %s (%d bytes)", path, len(html))


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def _val_after_label(soup: BeautifulSoup, label_text: str) -> str | None:
    """Find a 'CONSUMER DETAIL' style field: <span class="en-lbl">LABEL</span>
    ... followed later by a sibling <div class="val-space...">VALUE</div>."""
    label_span = soup.find("span", class_="en-lbl", string=lambda s: s and s.strip() == label_text)
    if not label_span:
        return None
    val_div = label_span.find_next("div", class_=lambda c: c and "val-space" in c)
    return _clean(val_div.get_text()) if val_div else None


def parse_bill(html: str, reference_no: str) -> BillResult:
    soup = BeautifulSoup(html, "html.parser")
    result = BillResult(reference_no=reference_no, fetched_at=datetime.now().isoformat())

    result.consumer_id = _val_after_label(soup, "CONSUMER ID")
    result.address = _val_after_label(soup, "NAME & ADDRESS")
    result.tariff_category = _val_after_label(soup, "TARIFF CATEGORY")
    result.units = _val_after_label(soup, "UNITS")

    # Address text contains "Name, Address..." — first comma-separated chunk is the name
    if result.address:
        result.consumer_name = result.address.split(",")[0].strip()

    # Grand Total: label span "Grand Total", value is the next charges-bd-val span
    grand_label = soup.find("span", class_="charges-bd-en", string=lambda s: s and s.strip() == "Grand Total")
    if grand_label:
        val_span = grand_label.find_next("span", class_="charges-bd-val")
        result.grand_total = _clean(val_span.get_text()) if val_span else None

    # Bill month: right-panel-en "BILL MONTH" -> next right-main-val div
    month_label = soup.find("span", class_="right-panel-en", string=lambda s: s and "BILL MONTH" in s)
    if month_label:
        val_div = month_label.find_next("div", class_=lambda c: c and "right-main-val" in c)
        result.bill_month = _clean(val_div.get_text()) if val_div else None

    # Reading / issue dates
    for label, attr in [("READING DATE", "reading_date"), ("ISSUE DATE", "issue_date")]:
        lbl = soup.find("span", string=lambda s: s and label in s)
        if lbl:
            val_span = lbl.find_next("span", class_="right-panel-date-val")
            setattr(result, attr, _clean(val_span.get_text()) if val_span else None)

    # Due date: right-panel-en--due -> next right-main-val--due div
    due_label = soup.find("span", class_=lambda c: c and "right-panel-en--due" in c)
    if due_label:
        val_div = due_label.find_next("div", class_=lambda c: c and "right-main-val--due" in c)
        result.due_date = _clean(val_div.get_text()) if val_div else None

    result.raw_found = any([result.grand_total, result.due_date, result.bill_month])
    if not result.raw_found:
        log.warning("Fields empty — PITC markup may have changed. Run dump_html() and re-check class names.")
    return result


def get_bill(reference_no: str) -> BillResult:
    html = fetch_raw_html(reference_no)
    return parse_bill(html, reference_no)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python iesco_bill_fetch.py <14-digit-reference-number>")
        sys.exit(1)

    ref_no = sys.argv[1]
    bill = get_bill(ref_no)
    print(json.dumps(asdict(bill), indent=2))
