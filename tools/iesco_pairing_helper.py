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
import sys
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4
import winreg

import requests
from invoices.iesco_bill_fetch import get_bill, active_vpn_adapter, VpnDetectedError

VERSION = "2.0"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "TMS" / "IESCO Helper"
DEVICE_FILE = APP_DIR / "device.json"
INSTALLED_EXE = APP_DIR / "TMS IESCO Fetch Helper.exe"
BUILD_FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "iesco_helper_build.json"


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
    return requests.request(method, api(path), headers=headers, timeout=25, **kwargs)


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
    return show("Computer connected successfully. You can now use Fetch or Fetch All.")


def fetch(host, query):
    try:
        token = _crypt(config()["credential"], False)
    except (KeyError, OSError, ValueError):
        return show("Helper installed but not connected. Click Connect This Computer in TMS.", True)
    try:
        result = call("GET", "references/", token)
        if result.status_code in (401, 403):
            return show("Device revoked or IESCO permission removed. Reconnect this computer.", True)
        result.raise_for_status()
        allowed = result.json()["references"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return show("Could not get the active IESCO reference list from TMS.", True)
    if not isinstance(allowed, list):
        return show("Invalid active reference list from TMS.", True)
    allowed = [ref for ref in allowed if isinstance(ref, str) and re.fullmatch(r"[0-9]{14}", ref)]
    reference = query.get("reference", [""])[0]
    if host == "fetch" and (not re.fullmatch(r"[0-9]{14}", reference) or reference not in allowed):
        return show("This IESCO reference is invalid or no longer active.", True)
    targets = [reference] if host == "fetch" else allowed
    if not targets:
        return show("TMS has no active IESCO references to fetch.", True)
    if active_vpn_adapter():
        record("vpn_detected")
        return show("VPN detected. Disconnect the VPN before fetching IESCO bills.", True)
    record("fetch_started")
    show(f"Fetching {len(targets)} IESCO bill(s). Click OK to continue.")
    failed = 0
    for ref in targets:
        try:
            # Recheck revocation and active status before each PITC request.
            fresh = call("GET", "references/", token)
            if fresh.status_code in (401, 403):
                record("device_revoked")
                return show("Device revoked or IESCO access removed. Reconnect this computer.", True)
            fresh.raise_for_status()
            fresh_refs = fresh.json()
            if not isinstance(fresh_refs, dict) or ref not in fresh_refs.get("references", []):
                failed += 1
                continue
            bill = get_bill(ref)
            if not bill.raw_found or not bill.bill_month:
                raise ValueError("Incomplete PITC bill")
            result = call("POST", "ingest/", token, json=asdict(bill))
            if result.status_code in (401, 403):
                return show("Device revoked or IESCO access removed. Reconnect this computer.", True)
            result.raise_for_status()
        except VpnDetectedError:
            record("vpn_detected")
            return show("VPN detected. Disconnect the VPN before fetching IESCO bills.", True)
        except (requests.RequestException, ValueError):
            failed += 1
    if failed:
        record("fetch_failed")
        return show(f"{failed} bill(s) could not be fetched. PITC may be unavailable; existing bills were kept.", True)
    record("fetch_complete")
    return show("Fetch complete. Bills were sent to TMS for review.")


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
