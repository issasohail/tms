"""Single-file Windows installer and one-request IESCO helper."""
import base64
import ctypes
from ctypes import wintypes
from dataclasses import asdict
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4
import winreg

import requests
from invoices.iesco_bill_fetch import (
    VpnDetectedError,
    active_vpn_adapter,
    fetch_raw_html,
    parse_bill,
)

VERSION = "2.3"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "TMS" / "IESCO Helper"
DEVICE_FILE = APP_DIR / "device.json"
INSTALLED_EXE = APP_DIR / "TMS IESCO Fetch Helper.exe"
BUILD_FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "iesco_helper_build.json"
PITC_BASE_URL = "https://bill.pitc.com.pk/"
MAX_PDF_BYTES = 10 * 1024 * 1024
PDF_PROFILE_DIR = APP_DIR / "edge-pdf-profile"


def record(event):
    """Record only fixed event names; never credentials, URLs, or bill data."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("tms_iesco_helper")
        if not logger.handlers:
            handler = RotatingFileHandler(APP_DIR / "helper.log", maxBytes=250000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        logger.info(event)
    except OSError:
        pass


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]


def _crypt(value, encrypt):
    raw = value.encode("ascii") if encrypt else base64.b64decode(value)
    buffer = ctypes.create_string_buffer(raw)
    incoming = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    output = Blob()
    operation = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not operation(ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(output)):
        raise OSError("Windows credential protection failed")
    try:
        result = ctypes.string_at(output.data, output.size)
        return base64.b64encode(result).decode("ascii") if encrypt else result.decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def show(text, error=False):
    ctypes.windll.user32.MessageBoxW(0, text, "TMS IESCO Fetch Helper", 0x10 if error else 0x40)
    return 1 if error else 0


def api(path):
    base = json.loads(BUILD_FILE.read_text(encoding="utf-8-sig"))["base_url"].rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Invalid built-in TMS address")
    return base + "/invoices/api/iesco-helper/" + path


def call(method, path, token=None, **kwargs):
    headers = {"Authorization": "Bearer " + token} if token else {}
    kwargs.setdefault("allow_redirects", False)
    response = requests.request(
        method, api(path), headers=headers, timeout=25, **kwargs
    )
    if 300 <= response.status_code < 400:
        raise requests.HTTPError(
            "TMS API redirected unexpectedly", response=response
        )
    return response


def config():
    try:
        return json.loads(DEVICE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(data):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DEVICE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    temporary.replace(DEVICE_FILE)


def edge_executable():
    candidates = [shutil.which("msedge"), shutil.which("msedge.exe")]
    for root_name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(root_name)
        if root:
            candidates.append(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise ValueError("Microsoft Edge is required to archive the original IESCO bill PDF")


def printable_pitc_html(html):
    """Keep PITC's page intact while making its relative assets resolvable."""
    if not isinstance(html, str) or "<html" not in html.lower():
        raise ValueError("PITC returned an invalid bill page")
    if re.search(r"<base\b", html, flags=re.IGNORECASE):
        return html
    updated, count = re.subn(
        r"(<head\b[^>]*>)",
        rf'\1<base href="{PITC_BASE_URL}">',
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    if not count:
        raise ValueError("PITC bill page has no document head")
    return updated


def capture_pitc_pdf(html):
    """Print PITC's rendered HTML with installed Edge and return PDF bytes."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    edge = edge_executable()
    with tempfile.TemporaryDirectory(prefix="pitc-pdf-", dir=APP_DIR) as folder:
        folder = Path(folder)
        html_path = folder / "bill.html"
        pdf_path = folder / "bill.pdf"
        PDF_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        html_path.write_text(printable_pitc_html(html), encoding="utf-8")
        command = [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--no-first-run",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=1000",
            f"--user-data-dir={PDF_PROFILE_DIR}",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode or not pdf_path.is_file():
            raise ValueError("Microsoft Edge could not print the IESCO bill")
        pdf = pdf_path.read_bytes()
    if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF_BYTES:
        raise ValueError("The generated IESCO PDF is invalid or exceeds 10 MB")
    return pdf


def install():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if Path(sys.executable).resolve() != INSTALLED_EXE.resolve():
        shutil.copy2(sys.executable, INSTALLED_EXE)
    key = r"Software\Classes\tms-iesco"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as protocol:
        winreg.SetValueEx(protocol, "", 0, winreg.REG_SZ, "URL:TMS IESCO Fetch Helper")
        winreg.SetValueEx(protocol, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key + r"\shell\open\command") as command:
        winreg.SetValueEx(command, "", 0, winreg.REG_SZ, f'"{INSTALLED_EXE}" "%1"')
    record("installed")
    return show("IESCO Helper installed successfully. Return to TMS and click Connect This Computer.")


def pair(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
        return show("Invalid pairing request.", True)
    data = config()
    device_id = data.get("device_id") or str(uuid4())
    try:
        response = call("POST", "pair/", json={"token": token, "device_id": device_id, "name": socket.gethostname()[:128], "version": VERSION})
        if response.status_code == 410:
            return show("Pairing expired. Click Connect This Computer again in TMS.", True)
        if response.status_code != 200:
            return show("Pairing failed. Try Connect This Computer again in TMS.", True)
        data["device_id"] = device_id
        data["credential"] = _crypt(response.json()["device_token"], True)
        save(data)
    except (OSError, ValueError, KeyError, requests.RequestException):
        record("pair_failed")
        return show("Could not connect to TMS. Check the internet connection and try again.", True)
    record("paired")
    run_id = response.json().get("run_id")
    if run_id:
        return fetch("fetch-all", {"run": [run_id]})
    return show("Computer connected successfully. Return to TMS to start fetching.")


def fetch(host, query):
    run_id = query.get("run", [""])[0]
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", run_id):
        return show("Open Fetch from the IESCO bill list in TMS.", True)
    try:
        token = _crypt(config()["credential"], False)
    except (KeyError, OSError, ValueError):
        return show("Helper installed but not connected. Click Connect This Computer in TMS.", True)
    state = {"total": 0, "completed": 0, "succeeded": 0, "failed": 0}

    def progress(status, total=0, completed=0, succeeded=0, failed=0, message=""):
        response = call("POST", f"runs/{run_id}/progress/", token,
                        json={"status": status, "total": total, "completed": completed,
                              "succeeded": succeeded, "failed": failed, "message": message})
        response.raise_for_status()
        state.update(total=total, completed=completed, succeeded=succeeded, failed=failed)

    def stop(message):
        try:
            progress("failed", **state, message=message)
        except requests.RequestException:
            pass
        return show(message, True)

    try:
        result = call("GET", "references/", token)
        if result.status_code in (401, 403):
            return stop("Device revoked or IESCO permission removed. Reconnect this computer.")
        result.raise_for_status()
        allowed = result.json()["references"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return stop("Could not get the active IESCO reference list from TMS.")
    if not isinstance(allowed, list):
        return stop("Invalid active reference list from TMS.")
    allowed = [ref for ref in allowed if isinstance(ref, str) and re.fullmatch(r"[0-9]{14}", ref)]
    reference = query.get("reference", [""])[0]
    if host == "fetch" and (not re.fullmatch(r"[0-9]{14}", reference) or reference not in allowed):
        return stop("This IESCO reference is invalid or no longer active.")
    targets = [reference] if host == "fetch" else allowed
    if not targets:
        return stop("TMS has no active IESCO references to fetch.")
    if active_vpn_adapter():
        record("vpn_detected")
        return stop("VPN detected. Disconnect the VPN before fetching IESCO bills.")
    record("fetch_started")
    total = len(targets)
    try:
        progress("running", total=total, message=f"Starting {total} bill(s)")
    except requests.RequestException:
        return show("TMS could not start the fetch progress. Try again.", True)
    failed = 0
    pdf_failed = 0
    archived = 0
    already_archived = 0
    for index, ref in enumerate(targets):
        try:
            # Recheck revocation and active status before each PITC request.
            fresh = call("GET", "references/", token)
            if fresh.status_code in (401, 403):
                record("device_revoked")
                return stop("Device revoked or IESCO access removed. Reconnect this computer.")
            fresh.raise_for_status()
            fresh_refs = fresh.json()
            if not isinstance(fresh_refs, dict) or ref not in fresh_refs.get("references", []):
                raise ValueError("Reference no longer active")
            raw_html = fetch_raw_html(ref)
            bill = parse_bill(raw_html, ref)
            if not bill.raw_found or not bill.bill_month:
                raise ValueError("Incomplete PITC bill")
            result = call("POST", "ingest/", token, json=asdict(bill))
            if result.status_code in (401, 403):
                return stop("Device revoked or IESCO access removed. Reconnect this computer.")
            result.raise_for_status()
            ingest_data = result.json()
            reading_id = ingest_data["id"]
            if ingest_data.get("pdf_exists"):
                already_archived += 1
            else:
                try:
                    pdf = capture_pitc_pdf(raw_html)
                    pdf_result = call(
                        "POST",
                        "pdf/",
                        token,
                        data={"reading_id": str(reading_id)},
                        files={
                            "bill_pdf": (
                                f"IESCO-{ref}-{bill.bill_month}.pdf",
                                pdf,
                                "application/pdf",
                            )
                        },
                    )
                    if pdf_result.status_code in (401, 403):
                        return stop(
                            "Device revoked or IESCO access removed. Reconnect this computer."
                        )
                    pdf_result.raise_for_status()
                    if pdf_result.json().get("status") == "exists":
                        already_archived += 1
                    else:
                        archived += 1
                except (
                    KeyError,
                    OSError,
                    subprocess.TimeoutExpired,
                    requests.RequestException,
                    ValueError,
                ):
                    pdf_failed += 1
                    record("pdf_archive_failed")
        except VpnDetectedError:
            record("vpn_detected")
            return stop("VPN detected. Disconnect the VPN before fetching IESCO bills.")
        except (KeyError, OSError, subprocess.TimeoutExpired, requests.RequestException, ValueError):
            failed += 1
        try:
            progress(
                "running",
                total,
                index + 1,
                index + 1 - failed,
                failed,
                f"Processed {index + 1} of {total}; archived {archived} PDF(s); "
                f"{pdf_failed} PDF issue(s)",
            )
        except requests.RequestException:
            return show("TMS could not update fetch progress. Check the connection and retry.", True)
    try:
        progress(
            "completed",
            total,
            total,
            total - failed,
            failed,
            f"Updated {total - failed} bill(s); archived {archived} PDF(s); "
            f"{already_archived} already stored; {pdf_failed} PDF issue(s); "
            f"{failed} bill fetch(es) failed.",
        )
    except requests.RequestException:
        return show("Bills were sent, but TMS could not finalize progress. Refresh the bill list.", True)
    if failed or pdf_failed:
        record("fetch_failed")
        return 1
    record("fetch_complete")
    return 0


def main():
    try:
        if len(sys.argv) == 1:
            return install()
        if len(sys.argv) != 2:
            return show("Open the helper from TMS.", True)
        link = urlsplit(sys.argv[1])
        if link.scheme != "tms-iesco" or link.netloc not in ("pair", "fetch", "fetch-all") or link.path not in ("", "/"):
            return show("Invalid TMS IESCO request.", True)
        query = parse_qs(link.query)
        if link.netloc == "pair":
            return pair(query.get("token", [""])[0])
        return fetch(link.netloc, query)
    except (OSError, ValueError, requests.RequestException):
        return show("IESCO Helper could not complete the request. Check the network and try again.", True)


if __name__ == "__main__":
    raise SystemExit(main())
