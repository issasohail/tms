import subprocess
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = "https://dbill.ptcl.net.pk/PTCLSearchInvoice.aspx"

PHONE_WITHOUT_AREA = "5921709"
ACCOUNT_ID = "100001354664"

OUT = Path("ptcl_curl_test")
OUT.mkdir(exist_ok=True)

COOKIE_FILE = OUT / "cookies.txt"
PAGE_FILE = OUT / "search_page.html"
CAPTCHA_FILE = OUT / "captcha.jpg"
RESULT_FILE = OUT / "result.html"
RESULT_TEXT = OUT / "result.txt"


def run_curl(args, capture=True):
    cmd = ["curl.exe", "--silent", "--show-error", "--location"] + args
    print("\nRUNNING:")
    print(" ".join(cmd))

    if capture:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            check=False,
        )
    else:
        result = subprocess.run(
            cmd,
            check=False,
        )

    if result.returncode != 0:
        stderr = (
            result.stderr.decode("utf-8", errors="replace")
            if result.stderr
            else ""
        )
        raise RuntimeError(
            f"curl failed with code {result.returncode}\n{stderr}"
        )

    return result.stdout if capture else b""


print("=" * 80)
print("PTCL CURL FLOW TEST")
print("=" * 80)

# -------------------------------------------------------------------
# 1. GET search page and save PTCL session cookies
# -------------------------------------------------------------------

html = run_curl([
    "--cookie-jar", str(COOKIE_FILE),
    "--cookie", str(COOKIE_FILE),
    "--user-agent",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    URL,
])

PAGE_FILE.write_bytes(html)

print("\nSaved initial PTCL page:")
print(PAGE_FILE.resolve())

soup = BeautifulSoup(html, "html.parser")

# -------------------------------------------------------------------
# 2. Extract ASP.NET hidden fields
# -------------------------------------------------------------------

hidden = {}

for inp in soup.find_all("input", {"type": "hidden"}):
    name = inp.get("name")
    if name:
        hidden[name] = inp.get("value", "")

required_hidden = [
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
]

print("\nASP.NET fields:")

for key in required_hidden:
    print(key, "FOUND" if hidden.get(key) else "MISSING")

if not hidden.get("__VIEWSTATE"):
    raise RuntimeError("PTCL page did not contain __VIEWSTATE.")

# -------------------------------------------------------------------
# 3. Find captcha URL
# -------------------------------------------------------------------

captcha_url = None

for img in soup.find_all("img"):
    src = img.get("src") or ""
    if "captcha" in src.lower():
        captcha_url = urljoin(URL, src)
        break

if not captcha_url:
    raise RuntimeError("Could not find PTCL captcha image.")

print("\nCaptcha URL:")
print(captcha_url)

# -------------------------------------------------------------------
# 4. Download captcha using SAME curl cookie jar
# -------------------------------------------------------------------

captcha_bytes = run_curl([
    "--cookie-jar", str(COOKIE_FILE),
    "--cookie", str(COOKIE_FILE),
    "--referer", URL,
    captcha_url,
])

CAPTCHA_FILE.write_bytes(captcha_bytes)

print("\nCaptcha saved:")
print(CAPTCHA_FILE.resolve())

print("\nOpen the captcha image and type the number/characters shown.")

captcha = input("Captcha: ").strip()

if not captcha:
    raise RuntimeError("Captcha cannot be empty.")

# -------------------------------------------------------------------
# 5. POST PTCL form with ASP.NET state and captcha
# -------------------------------------------------------------------

post_args = [
    "--cookie-jar", str(COOKIE_FILE),
    "--cookie", str(COOKIE_FILE),
    "--referer", URL,
    "--user-agent",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
]

fields = {
    "__EVENTTARGET": hidden.get("__EVENTTARGET", ""),
    "__EVENTARGUMENT": hidden.get("__EVENTARGUMENT", ""),
    "__VIEWSTATE": hidden.get("__VIEWSTATE", ""),
    "__VIEWSTATEGENERATOR": hidden.get("__VIEWSTATEGENERATOR", ""),
    "__EVENTVALIDATION": hidden.get("__EVENTVALIDATION", ""),
    "ctl00$ContentPlaceHolder1$txtPhoneNo": PHONE_WITHOUT_AREA,
    "ctl00$ContentPlaceHolder1$txtAccountID": ACCOUNT_ID,
    "ctl00$ContentPlaceHolder1$txtCaptcha": captcha,
    "ctl00$ContentPlaceHolder1$btnVerify": "Search",
}

for key, value in fields.items():
    post_args += [
        "--data-urlencode",
        f"{key}={value}",
    ]

post_args += [URL]

result_html = run_curl(post_args)

RESULT_FILE.write_bytes(result_html)

result_soup = BeautifulSoup(result_html, "html.parser")
text = result_soup.get_text("\n", strip=True)

RESULT_TEXT.write_text(text, encoding="utf-8")

print("\n" + "=" * 80)
print("RESULT")
print("=" * 80)

print(text[:12000])

print("\nSaved HTML:")
print(RESULT_FILE.resolve())

print("\nSaved text:")
print(RESULT_TEXT.resolve())

if "Billing Month" in text and "Amount Due" in text:
    print("\nSUCCESS: PTCL bill was returned through curl.exe.")
else:
    print(
        "\nPTCL responded, but a bill was not detected. "
        "Check the result text for captcha or account errors."
    )
