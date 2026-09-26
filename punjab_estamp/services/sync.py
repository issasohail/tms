from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from django.db import transaction
from django.utils import timezone

from punjab_estamp.models import PunjabEStampDistrict, PunjabEStampTehsil

PUNJAB_ORIGIN = "https://es.punjab-zameen.gov.pk"
PUNJAB_CHALLAN_URL = (
    PUNJAB_ORIGIN
    + "/eStampCitizenPortal/ChallanFormView/AddChallanForWhitePaper"
    + "?name=GenerateChallan&vCount=49985&agree=true"
)
DEFAULT_TIMEOUT = 30
STALE_AFTER_DAYS = 30


class PunjabEStampSyncError(RuntimeError):
    pass


class _CurlResponse:
    def __init__(self, *, status_code: int, text: str, headers=None, url: str = ""):
        self.status_code = int(status_code or 0)
        self.text = text or ""
        self.headers = headers or {}
        self.url = url

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(
                f"Punjab e-Stamp returned HTTP {self.status_code} for {self.url}",
                response=self,
            )


class _PortalSession:
    """requests-compatible session with an automatic curl transport fallback."""

    def __init__(self):
        self._requests = requests.Session()
        self.headers = self._requests.headers
        self._curl_only = False
        cookie = tempfile.NamedTemporaryFile(prefix="punjab_estamp_", suffix=".cookies", delete=False)
        cookie.close()
        self._cookie_path = cookie.name

    def close(self):
        self._requests.close()
        try:
            os.unlink(self._cookie_path)
        except OSError:
            pass

    def _request(self, method, url, *, params=None, data=None, headers=None, timeout=None):
        timeout = int(timeout or DEFAULT_TIMEOUT)
        if not self._curl_only:
            try:
                return self._requests.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                )
            except requests.RequestException:
                self._curl_only = True

        return self._curl_request(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            timeout=timeout,
        )

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)

    def _curl_request(self, method, url, *, params=None, data=None, headers=None, timeout=DEFAULT_TIMEOUT):
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise requests.RequestException("curl executable is not available for Punjab e-Stamp fallback")

        if params:
            parsed = urlparse(url)
            query = list(parse_qsl(parsed.query, keep_blank_values=True))
            query.extend((str(key), str(value)) for key, value in params.items())
            url = urlunparse(parsed._replace(query=urlencode(query)))

        header_file = tempfile.NamedTemporaryFile(prefix="punjab_estamp_", suffix=".headers", delete=False)
        body_file = tempfile.NamedTemporaryFile(prefix="punjab_estamp_", suffix=".body", delete=False)
        header_file.close()
        body_file.close()

        command = [
            curl,
            "-sS",
            "-L",
            "--max-time",
            str(timeout),
            "--connect-timeout",
            str(min(timeout, 10)),
            "-b",
            self._cookie_path,
            "-c",
            self._cookie_path,
            "-D",
            header_file.name,
            "-o",
            body_file.name,
            "-w",
            "%{http_code}",
        ]

        merged_headers = dict(self.headers)
        if headers:
            merged_headers.update(headers)
        request_cookies = self._requests.cookies.get_dict()
        if request_cookies and not any(key.casefold() == "cookie" for key in merged_headers):
            merged_headers["Cookie"] = "; ".join(
                f"{key}={value}" for key, value in request_cookies.items()
            )
        for key, value in merged_headers.items():
            if value is None:
                continue
            command.extend(["-H", f"{key}: {value}"])

        if method.upper() == "POST":
            command.extend(["-X", "POST"])
            if data:
                for key, value in data.items():
                    command.extend(["--data-urlencode", f"{key}={value}"])
            else:
                # The Punjab portal rejects a body-less POST with HTTP 411.
                # Sending an explicit empty payload makes curl emit Content-Length: 0,
                # matching the Kendo transport used by the live portal.
                command.extend(["--data-raw", ""])

        command.append(url)

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout + 5,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or "curl transport failed").strip()
                raise requests.RequestException(detail)

            try:
                status_code = int((completed.stdout or "0").strip()[-3:])
            except ValueError as exc:
                raise requests.RequestException("curl did not return an HTTP status code") from exc

            body = Path(body_file.name).read_text(encoding="utf-8", errors="replace")
            raw_headers = Path(header_file.name).read_text(encoding="utf-8", errors="replace")
            response_headers = {}
            for line in raw_headers.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                response_headers[key.strip()] = value.strip()

            return _CurlResponse(
                status_code=status_code,
                text=body,
                headers=response_headers,
                url=url,
            )
        finally:
            for path in (header_file.name, body_file.name):
                try:
                    os.unlink(path)
                except OSError:
                    pass


@dataclass(frozen=True)
class TehsilOption:
    name: str
    portal_value: str


def district_cache_is_stale(district: PunjabEStampDistrict, *, now=None) -> bool:
    """Seeded/cached rows with no live timestamp remain usable until explicit refresh."""
    if not district.last_synced_at:
        return False
    now = now or timezone.now()
    return district.last_synced_at < now - timedelta(days=STALE_AFTER_DAYS)


def _clean_option(name, value):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    value = str(value or "").strip()
    if not name or not value:
        return None
    lowered = name.casefold()
    if lowered in {"select", "select tehsil", "-- select --", "choose tehsil"}:
        return None
    if value in {"0", "-1"} and lowered.startswith(("select", "choose")):
        return None
    return TehsilOption(name=name, portal_value=value)


def _dedupe(options: Iterable[TehsilOption]) -> list[TehsilOption]:
    seen = set()
    result = []
    for option in options:
        key = option.portal_value
        if key in seen:
            continue
        seen.add(key)
        result.append(option)
    return result


def _options_from_json(payload) -> list[TehsilOption]:
    candidates = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        lowered = {str(k).casefold(): v for k, v in value.items()}
        value_keys = (
            "value",
            "id",
            "tehsilid",
            "tehsil_id",
            "tehsilcode",
            "code",
            "portal_value",
        )
        name_keys = (
            "text",
            "name",
            "tehsilname",
            "tehsil_name",
            "label",
        )
        portal_value = next((lowered[k] for k in value_keys if k in lowered), None)
        name = next((lowered[k] for k in name_keys if k in lowered), None)
        option = _clean_option(name, portal_value)
        if option:
            candidates.append(option)

        for nested_key in ("data", "result", "results", "items", "tehsils", "list"):
            if nested_key in lowered:
                visit(lowered[nested_key])

    visit(payload)
    return _dedupe(candidates)


def _options_from_html(text: str) -> list[TehsilOption]:
    soup = BeautifulSoup(text or "", "html.parser")
    candidates = []
    for select in soup.find_all("select"):
        select_id = (select.get("id") or select.get("name") or "").casefold()
        if "tehsil" not in select_id:
            continue
        for option_tag in select.find_all("option"):
            option = _clean_option(option_tag.get_text(" ", strip=True), option_tag.get("value"))
            if option:
                candidates.append(option)
    return _dedupe(candidates)


def _parse_tehsil_response(response) -> list[TehsilOption]:
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "json" in content_type:
        try:
            options = _options_from_json(response.json())
        except (ValueError, json.JSONDecodeError):
            options = []
        if options:
            return options

    text = response.text or ""
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if payload is not None:
        options = _options_from_json(payload)
        if options:
            return options

    return _options_from_html(text)


def _same_origin(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.netloc.casefold() == "es.punjab-zameen.gov.pk"


def _discover_candidate_urls(html: str, session, timeout: int) -> list[str]:
    """Discover current Tehsil AJAX URLs instead of hard-coding one portal endpoint."""
    soup = BeautifulSoup(html or "", "html.parser")
    sources = [html or ""]

    script_urls = []
    for script in soup.find_all("script", src=True):
        url = urljoin(PUNJAB_CHALLAN_URL, script.get("src"))
        if not _same_origin(url):
            continue
        marker = url.casefold()
        if any(word in marker for word in ("challan", "stamp", "form", "citizen", "common")):
            script_urls.append(url)

    for url in script_urls[:12]:
        try:
            response = session.get(url, timeout=timeout)
            if response.ok:
                sources.append(response.text or "")
        except requests.RequestException:
            continue

    candidates = []
    # URLs may appear as quoted MVC action paths or as complete URLs.
    patterns = (
        r"['\"]([^'\"]{1,300}tehsil[^'\"]{0,300})['\"]",
        r"url\s*:\s*['\"]([^'\"]+)['\"]",
    )
    for source in sources:
        for pattern in patterns:
            for raw in re.findall(pattern, source, flags=re.IGNORECASE):
                if "tehsil" not in raw.casefold():
                    continue
                url = urljoin(PUNJAB_CHALLAN_URL, raw.replace("&amp;", "&"))
                if _same_origin(url):
                    candidates.append(url)

    # Conservative fallbacks are endpoint-name guesses only; no data IDs are fabricated.
    base = PUNJAB_ORIGIN + "/eStampCitizenPortal/ChallanFormView/"
    candidates.extend(
        [
            base + "GetTehsil",
            base + "GetTehsilList",
            base + "GetTehsils",
            base + "GetTehsilByDistrict",
            base + "GetTehsilByDistrictId",
            base + "GetTehsilByDistrictID",
        ]
    )

    unique = []
    seen = set()
    for url in candidates:
        normalized = url.split("#", 1)[0]
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _request_with_district(
    session,
    url: str,
    district_portal_value: str,
    *,
    timeout: int,
    request_verification_token: str = "",
):
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": PUNJAB_CHALLAN_URL,
        "X-Requested-With": "XMLHttpRequest",
    }
    if request_verification_token:
        headers["RequestVerificationToken"] = request_verification_token

    parsed = urlparse(url)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    parameter_names = ("DistrictId", "districtId", "DistrictID", "districtID", "District")
    attempts = []

    # If the discovered URL already contains a district-like parameter, replace it first.
    existing_district_key = next((k for k in existing if "district" in k.casefold()), None)
    if existing_district_key:
        query = dict(existing)
        query[existing_district_key] = district_portal_value
        attempts.append(("get", urlunparse(parsed._replace(query=urlencode(query))), None))

    for key in parameter_names[:3]:
        attempts.append(("get", url, {key: district_portal_value}))
        attempts.append(("post", url, {key: district_portal_value}))

    for method, request_url, values in attempts:
        try:
            if method == "get":
                response = session.get(
                    request_url,
                    params=values,
                    headers=headers,
                    timeout=timeout,
                )
            else:
                response = session.post(
                    request_url,
                    data=values,
                    headers=headers,
                    timeout=timeout,
                )
        except requests.RequestException:
            continue
        if not response.ok:
            continue
        options = _parse_tehsil_response(response)
        if options:
            return options
    return []


def fetch_tehsil_options(district: PunjabEStampDistrict, *, timeout=DEFAULT_TIMEOUT) -> list[TehsilOption]:
    """Fetch current public Tehsil options for one configured Punjab District."""
    if not district.portal_value:
        raise PunjabEStampSyncError("The selected District has no Punjab portal value.")

    session = _PortalSession()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            )
        }
    )
    try:
        try:
            page = session.get(PUNJAB_CHALLAN_URL, timeout=timeout)
            page.raise_for_status()
        except requests.RequestException as exc:
            raise PunjabEStampSyncError(
                "Punjab e-Stamp portal could not be reached with either the Python HTTP "
                "transport or the curl fallback. If you are using a VPN or proxy, disconnect "
                "it and try again. Also check your internet connection and confirm that the "
                "Punjab e-Stamp website opens directly in your browser."
            ) from exc

        soup = BeautifulSoup(page.text or "", "html.parser")
        token_input = soup.find("input", attrs={"name": "__RequestVerificationToken"})
        verification_token = token_input.get("value", "") if token_input else ""

        # Some portal versions may render options directly after a district-specific request.
        direct = _options_from_html(page.text or "")
        if direct:
            return direct

        # Verified from the live White Paper portal JavaScript (populateTehsils):
        # POST /api/Proxy/Locations/TehsilsByDistrictId?Id=<district portal id>.
        # The endpoint requires an explicit zero-length POST body.
        verified_url = (
            PUNJAB_ORIGIN
            + "/eStampCitizenPortal/api/Proxy/Locations/TehsilsByDistrictId"
            + "?Id="
            + str(district.portal_value)
        )
        verified_headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": PUNJAB_CHALLAN_URL,
            "X-Requested-With": "XMLHttpRequest",
        }
        if verification_token:
            verified_headers["RequestVerificationToken"] = verification_token

        try:
            verified_response = session.post(
                verified_url,
                data={},
                headers=verified_headers,
                timeout=timeout,
            )
            if verified_response.ok:
                verified_options = _parse_tehsil_response(verified_response)
                if verified_options:
                    return verified_options
        except requests.RequestException:
            pass

        # Keep discovery as a compatibility fallback in case the portal changes again.
        for candidate in _discover_candidate_urls(page.text or "", session, timeout):
            options = _request_with_district(
                session,
                candidate,
                str(district.portal_value),
                timeout=timeout,
                request_verification_token=verification_token,
            )
            if options:
                return options

        raise PunjabEStampSyncError(
            "Punjab e-Stamp responded, but its Tehsil data endpoint could not be read. "
            "Use the cached Tehsil list if available or import a verified Punjab option file."
        )
    finally:
        session.close()


@transaction.atomic
def sync_district_tehsils(
    district: PunjabEStampDistrict,
    *,
    dry_run=False,
    mark_missing_inactive=False,
    timeout=DEFAULT_TIMEOUT,
):
    options = fetch_tehsil_options(district, timeout=timeout)
    if not options:
        raise PunjabEStampSyncError("Punjab returned no Tehsils for the selected District.")

    existing = {
        row.portal_value: row
        for row in PunjabEStampTehsil.objects.select_for_update().filter(district=district)
    }
    created = 0
    updated = 0
    unchanged = 0
    seen_values = set()

    for index, option in enumerate(options, start=1):
        seen_values.add(option.portal_value)
        row = existing.get(option.portal_value)
        desired_order = index
        if row is None:
            created += 1
            if not dry_run:
                PunjabEStampTehsil.objects.create(
                    district=district,
                    name=option.name,
                    portal_value=option.portal_value,
                    active=True,
                    sort_order=desired_order,
                )
            continue

        changes = {}
        if row.name != option.name:
            changes["name"] = option.name
        if not row.active:
            changes["active"] = True
        if row.sort_order != desired_order:
            changes["sort_order"] = desired_order
        if changes:
            updated += 1
            if not dry_run:
                for field, value in changes.items():
                    setattr(row, field, value)
                row.save(update_fields=list(changes))
        else:
            unchanged += 1

    deactivated = 0
    if mark_missing_inactive:
        missing = [row for value, row in existing.items() if value not in seen_values and row.active]
        deactivated = len(missing)
        if not dry_run:
            for row in missing:
                row.active = False
                row.save(update_fields=["active"])

    synced_at = timezone.now()
    if not dry_run:
        district.last_synced_at = synced_at
        district.save(update_fields=["last_synced_at"])

    return {
        "district_id": district.pk,
        "district": district.name,
        "district_portal_value": district.portal_value,
        "found": len(options),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "deactivated": deactivated,
        "dry_run": bool(dry_run),
        "synced_at": synced_at,
    }
