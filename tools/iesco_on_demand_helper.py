"""One-job Windows launcher for authorised Pakistani TMS users.

Invoked only by a tms-iesco://fetch?reference=<14 digits> browser link.  It
uses the existing local PITC fetch module, which uploads the parsed bill to
the configured production ingest API, and exits as soon as that job finishes.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("APPDATA", Path.home())) / "TMS" / "iesco_helper.json"


def fail(message: str) -> int:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "TMS IESCO Fetch", 0x10)
    except Exception:
        pass
    print(message, file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != 2:
        return fail("This helper must be opened from the TMS Fetch button.")
    parsed = urllib.parse.urlparse(sys.argv[1])
    reference = urllib.parse.parse_qs(parsed.query).get("reference", [""])[0]
    if parsed.scheme != "tms-iesco" or parsed.netloc not in {"fetch", "fetch-all"}:
        return fail("Invalid TMS IESCO fetch request.")
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        ingest_url = str(config["ingest_url"])
        references_url = str(config.get("references_url", ""))
        api_key = str(config["api_key"])
    except (OSError, KeyError, ValueError, TypeError):
        return fail("IESCO helper is not configured. Run the installer again.")
    if not ingest_url.startswith("https://") or not api_key:
        return fail("IESCO helper configuration is incomplete.")
    if parsed.netloc == "fetch" and not re.fullmatch(r"\d{14}", reference):
        return fail("Invalid IESCO reference number.")
    if parsed.netloc == "fetch-all":
        if not references_url.startswith("https://"):
            return fail("Fetch All is not configured. Run the helper installer again.")
        import requests
        try:
            response = requests.get(references_url, headers={"X-API-Key": api_key}, timeout=20)
            response.raise_for_status()
            references = response.json().get("references", [])
        except requests.RequestException:
            return fail("Could not get the active reference list from TMS.")
        if not isinstance(references, list) or not references:
            return fail("TMS has no active IESCO references to fetch.")
    else:
        references = [reference]
    os.environ["TMS_IESCO_INGEST_URL"] = ingest_url
    os.environ["TMS_IESCO_API_KEY"] = api_key
    from invoices.iesco_bill_fetch import get_bill, push_to_server
    failed = []
    for item in references:
        if not isinstance(item, str) or not re.fullmatch(r"\d{14}", item):
            continue
        try:
            bill = get_bill(item)
            if not bill.raw_found:
                raise RuntimeError("PITC returned an incomplete bill page")
            push_to_server(bill)
        except Exception:
            failed.append(item)
    if failed:
        return fail(f"{len(failed)} bill(s) could not be fetched. Check VPN/network and retry.")
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, "Bill fetched and sent to TMS for review.", "TMS IESCO Fetch", 0x40)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
