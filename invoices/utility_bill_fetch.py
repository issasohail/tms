
import base64, re
import subprocess
import tempfile
import shutil
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import UtilityBillReading

HEADERS={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153.0.0.0 Safari/537.36"}
SNGPL_URL="https://www.sngpl.com.pk/viewbill?artcl=artuyh709123465&cats=ct456712337&client=ANDROID&consumer={consumer}&contype=NewCon&mdids=85&pgname=PAGES_NAME&proc=viewbill&secs=ss7xa852op845"
PTCL_URL="https://dbill.ptcl.net.pk/PTCLSearchInvoice.aspx"

def _ptcl_curl_executable():
    exe = shutil.which("curl.exe") or shutil.which("curl")
    if not exe:
        raise ValidationError(
            "PTCL fetching requires curl, but curl was not found on this computer."
        )
    return exe


def _ptcl_curl_request(method, url, cookie_jar="", data=None, referer=None):
    # PTCL's DBill TLS endpoint succeeds through system curl on this machine
    # while requests/urllib3 can fail with SSLEOFError.
    curl = _ptcl_curl_executable()

    with tempfile.TemporaryDirectory(prefix="tms_ptcl_") as tmp:
        tmpdir = Path(tmp)
        cookie_path = tmpdir / "cookies.txt"
        body_path = tmpdir / "body.bin"

        if cookie_jar:
            cookie_path.write_text(cookie_jar, encoding="utf-8")

        cmd = [
            curl,
            "--silent",
            "--show-error",
            "--location",
            "--connect-timeout", "20",
            "--max-time", "60",
            "--cookie-jar", str(cookie_path),
            "--user-agent",
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/153.0.0.0 Safari/537.36"
            ),
            "--output", str(body_path),
            "--write-out", "%{url_effective}",
        ]

        if cookie_path.exists():
            cmd += ["--cookie", str(cookie_path)]

        if referer:
            cmd += ["--referer", referer]

        if method.upper() == "POST":
            cmd += ["--request", "POST"]
            for key, value in (data or {}).items():
                cmd += ["--data-urlencode", f"{key}={value}"]

        cmd.append(url)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            err = (result.stderr or "").strip()
            raise ValidationError(
                "PTCL bill service could not be reached. "
                f"curl error {result.returncode}: {err}"
            )

        if not body_path.exists():
            raise ValidationError("PTCL returned no response body.")

        body = body_path.read_bytes()

        new_cookie_jar = ""
        if cookie_path.exists():
            new_cookie_jar = cookie_path.read_text(
                encoding="utf-8",
                errors="replace",
            )

        effective_url = (result.stdout or "").strip()
        return body, new_cookie_jar, effective_url



def _ptcl_session():
    """PTCL-only retries for intermittent DBill TLS/connection failures."""
    session = requests.Session()
    session.headers.update({**HEADERS, "Connection": "close", "Referer": PTCL_URL})
    retry = Retry(
        total=4, connect=4, read=3, status=2, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    session.mount("https://dbill.ptcl.net.pk/", HTTPAdapter(max_retries=retry))
    return session


def _ptcl_get(session, url):
    try:
        response = session.get(url, timeout=45)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = session.get(url, timeout=45, verify=False)
        response.raise_for_status()
        return response


def _ptcl_post(session, url, **kwargs):
    try:
        response = session.post(url, timeout=45, **kwargs)
        response.raise_for_status()
        return response
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        response = session.post(url, timeout=45, verify=False, **kwargs)
        response.raise_for_status()
        return response

def money(v):
    c=re.sub(r"[^0-9.\-]","",str(v or "").replace(",",""))
    if c in ("","-",".","-."): return None
    try: return Decimal(c)
    except InvalidOperation: return None

def first(p,t,default=""):
    m=re.search(p,t,re.I|re.S)
    return m.group(1).strip() if m else default

def fetch_sngpl(account):
    consumer=re.sub(r"\D","",account.consumer_number or "")
    s=requests.Session(); s.headers.update(HEADERS)
    r=s.get(SNGPL_URL.format(consumer=consumer),timeout=35); r.raise_for_status()
    soup=BeautifulSoup(r.text,"html.parser"); text=soup.get_text(" ",strip=True)
    if consumer not in text: raise ValidationError("SNGPL did not return this consumer number.")
    month=first(rf"{re.escape(consumer)}\s+.*?([A-Za-z]{{3}}\s+\d{{4}})",text) or first(r"([A-Za-z]{3}\s+\d{4})",text)
    # SNGPL summary tables often expose the due date without a literal
    # "Due Date:" label. Typical sequence:
    # consumer | billing month | amount due | due date | amount after due.
    sngpl_due_date = ""
    sngpl_amount_after_due = None

    for table in soup.find_all("table"):
        table_text = table.get_text(" ", strip=True)
        if consumer not in table_text:
            continue

        # Prefer the compact consumer/billing-month summary table and avoid
        # the meter-detail table containing current/previous reading dates.
        if "Current Previous Difference" in table_text or "Reading:" in table_text:
            continue

        dates = re.findall(r"\b\d{2}-\d{2}-\d{4}\b", table_text)
        if not dates:
            continue

        sngpl_due_date = dates[0]
        tail = table_text.split(sngpl_due_date, 1)[1]
        after_match = re.search(r"(?<!\d)(-?[\d,]+(?:\.\d+)?)(?!\d)", tail)
        if after_match:
            sngpl_amount_after_due = money(after_match.group(1))
        break

    if not sngpl_due_date:
        sngpl_due_date = first(
            r"Due Date:?\s*([0-9]{2}-[0-9]{2}-[0-9]{4})",
            text,
        )

    m=re.search(r"Dates:\s*([0-9-]+)\s*([0-9-]+).*?Reading:\s*([0-9.]+)\s*([0-9.]+)\s*([0-9.]+)",text,re.I|re.S)
    cr=pr=diff=None; crd=prd=""
    if m:
        crd,prd=m.group(1),m.group(2); cr,pr,diff=money(m.group(3)),money(m.group(4)),money(m.group(5))
    def lm(label,nextlabel=None):
        p=rf"{label}.*?(-?[\d,]+(?:\.\d+)?)"
        if nextlabel: p += rf"\s+{nextlabel}"
        return money(first(p,text))
    current_bill=lm("Current Bill","Arrears")
    arrears=lm(r"Arrears\s*/\s*Aging","Late Payment")
    total=lm("Total Amount Due")
    hm3=money(first(r"Gas Consumed HM3\s*([0-9.]+)",text))
    service={
        "gas_charges": str(lm("Gas Charges","Prov") or ""),
        "meter_rent": str(lm("Meter Rent","Fixed Charges") or ""),
        "gst": str(lm("GST","Rebate") or ""),
        "mmbtu": first(r"\*MMBTU\s*([0-9.]+)",text),
        "gcv": first(r"GCV\s*([0-9.]+)",text),
        "current_reading_date":crd,"previous_reading_date":prd,
    }
    hist=[]
    for table in soup.find_all("table"):
        rows=table.find_all("tr")
        for row in rows:
            vals=[c.get_text(" ",strip=True) for c in row.find_all(["th","td"])]
            if len(vals)>=5 and re.match(r"^[A-Za-z]{3}\s+\d{4}$",vals[0] or ""):
                hist.append({"month":vals[0],"hm3":vals[1],"current_bill":vals[2],"amount_due":vals[3],"payment":vals[4]})
    if not month: raise ValidationError("Could not identify SNGPL billing month.")
    obj,_=UtilityBillReading.objects.update_or_create(account=account,bill_month=month,defaults={
        "issue_date":first(r"Issue Date:\s*([0-9-]+)",text),
        "due_date": sngpl_due_date,
        "amount_due":total,"amount_after_due":sngpl_amount_after_due,"current_bill":current_bill,"arrears":arrears,
        "previous_reading":pr,"current_reading":cr,"consumption":hm3 if hm3 is not None else diff,
        "consumption_unit":"HM3","service_details":service,"bill_history":hist,
        "raw_data":{"consumer_number":consumer},"fetched_at":timezone.now(),
    })
    return obj

def start_ptcl_captcha(account):
    page_bytes, cookie_jar, effective_url = _ptcl_curl_request(
        "GET",
        PTCL_URL,
    )

    html = page_bytes.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    hidden = {}
    for x in soup.find_all("input", {"type": "hidden"}):
        if x.get("name"):
            hidden[x["name"]] = x.get("value", "")

    if not hidden.get("__VIEWSTATE"):
        raise ValidationError(
            "PTCL returned an unexpected page without form state."
        )

    cap = None
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if "captcha" in src.lower():
            cap = urljoin(PTCL_URL, src)
            break

    if not cap:
        raise ValidationError("PTCL captcha image not found.")

    image_bytes, cookie_jar, _ = _ptcl_curl_request(
        "GET",
        cap,
        cookie_jar=cookie_jar,
        referer=effective_url or PTCL_URL,
    )

    content_type = "image/jpeg"
    if image_bytes.startswith(b"\x89PNG"):
        content_type = "image/png"
    elif image_bytes.startswith((b"GIF87a", b"GIF89a")):
        content_type = "image/gif"

    data = (
        f"data:{content_type};base64,"
        f"{base64.b64encode(image_bytes).decode()}"
    )

    # Preserve the same keys already expected by views_utility.py.
    return {
        "hidden": hidden,
        "cookies": cookie_jar,
        "image": data,
    }


def finish_ptcl(account,captcha,state):
    captcha = (captcha or "").strip()
    if not captcha:
        raise ValidationError("Enter the PTCL captcha code.")

    digits = re.sub(r"\D", "", account.ptcl_phone or "")
    phone = (
        digits[3:]
        if digits.startswith("0") and len(digits) >= 10
        else digits
    )

    payload = dict(state.get("hidden") or {})
    payload.update({
        "ctl00$ContentPlaceHolder1$txtPhoneNo": phone,
        "ctl00$ContentPlaceHolder1$txtAccountID": account.ptcl_account_id,
        "ctl00$ContentPlaceHolder1$txtCaptcha": captcha,
        "ctl00$ContentPlaceHolder1$btnVerify": "Search",
    })

    result_bytes, cookie_jar, final_url = _ptcl_curl_request(
        "POST",
        PTCL_URL,
        cookie_jar=state.get("cookies") or "",
        data=payload,
        referer=PTCL_URL,
    )

    result_html = result_bytes.decode("utf-8", errors="replace")
    text = BeautifulSoup(
        result_html,
        "html.parser",
    ).get_text(" ", strip=True)

    if "Billing Month" not in text:
        if "captcha" in text.lower():
            raise ValidationError(
                "PTCL captcha was rejected. Load a new captcha and try again."
            )
        raise ValidationError(
            "PTCL did not return a bill for this account."
        )

    month = first(
        r"Billing Month\s*([A-Za-z]{3}-\d{4})",
        text,
    )
    if not month:
        raise ValidationError("Could not identify PTCL billing month.")

    labels = {
        "bundle": r"Bundle\s*Rs\.\s*([\d,.-]+)",
        "tv": r"TV\s*Rs\.\s*([\d,.-]+)",
        "vas_paper_bill": r"VAS/Paper Bill\s*Rs\.\s*([\d,.-]+)",
        "total_service_charges": r"Total Service Charges\s*Rs\.\s*([\d,.-]+)",
        "arrears": r"Arrears\s*Rs\.\s*([\d,.-]+)",
        "credit": r"Credit\s*Rs\.\s*([\d,.-]+)",
        "sales_tax": r"FED/Sales Tax\s*Rs\.\s*([\d,.-]+)",
        "withholding_tax": r"W\.H\.Tax\s*Rs\.\s*([\d,.-]+)",
        "late_payment_surcharge": r"Late Pay Surcharge\s*Rs\.\s*([\d,.-]+)",
        "grand_total": r"Grand Total\s*Rs\.\s*([\d,.-]+)",
    }

    service = {}
    for key, pattern in labels.items():
        value = money(first(pattern, text))
        service[key] = str(value) if value is not None else ""

    service["package_speed"] = first(
        r"(\d+\s*Mbps)\s+01-31",
        text,
    )

    usage = re.search(
        r"Usage:\s*([\d.]+)\s*/\s*([\d.]+)\s*GB",
        text,
        re.I,
    )
    if usage:
        service.update({
            "usage_gb": usage.group(1),
            "usage_limit_gb": usage.group(2),
        })

    due = money(
        first(
            r"Amount Due\s*Rs\.\s*([\d,.-]+)",
            text,
        )
    )
    after = money(
        first(
            r"Amount After Due Date\s*Rs\.\s*([\d,.-]+)",
            text,
        )
    )
    arrears = money(service.get("arrears"))

    obj, _ = UtilityBillReading.objects.update_or_create(
        account=account,
        bill_month=month,
        defaults={
            "issue_date": first(
                r"Issue Date:\s*([0-9-]+)",
                text,
            ),
            "due_date": first(
                r"Due Date\s*([0-9-]+)",
                text,
            ),
            "amount_due": due,
            "amount_after_due": after,
            "current_bill": due,
            "arrears": arrears,
            "invoice_number": first(
                r"Invoice\s*#\s*([0-9]+)",
                text,
            ),
            "service_details": service,
            "raw_data": {
                "final_url": final_url,
            },
            "fetched_at": timezone.now(),
        },
    )
    return obj
