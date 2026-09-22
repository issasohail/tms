from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://dbill.ptcl.net.pk/PTCLSearchInvoice.aspx"

PHONE = "5921709"
ACCOUNT_ID = "100001354664"

OUT = Path("ptcl_test")
OUT.mkdir(exist_ok=True)

session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        )
    }
)

print("Loading PTCL DBill page...")

r = session.get(URL, timeout=30)
r.raise_for_status()

soup = BeautifulSoup(r.text, "html.parser")

print("Status:", r.status_code)
print("Page:", r.url)

# Save HTML
(OUT / "initial.html").write_text(r.text, encoding="utf-8")


# ------------------------------------------------------------
# ASP.NET hidden state
# ------------------------------------------------------------

hidden = {}

for inp in soup.find_all("input", {"type": "hidden"}):
    name = inp.get("name")

    if name:
        hidden[name] = inp.get("value", "")

print("\nASP.NET hidden fields:")

for k in hidden:
    print(" ", k)


# ------------------------------------------------------------
# Find captcha image
# ------------------------------------------------------------

print("\nImages found:")

captcha_url = None

for img in soup.find_all("img"):
    src = img.get("src")

    if not src:
        continue

    full = urljoin(URL, src)

    print(" ", full)

    src_lower = src.lower()

    if "captcha" in src_lower:
        captcha_url = full


if captcha_url:
    print("\nCaptcha image:", captcha_url)

    img = session.get(captcha_url, timeout=30)
    img.raise_for_status()

    captcha_file = OUT / "captcha.jpg"
    captcha_file.write_bytes(img.content)

    print("Captcha saved to:")
    print(captcha_file.resolve())

else:
    print("\nNo obvious captcha image found.")


# ------------------------------------------------------------
# Ask user to manually read captcha
# ------------------------------------------------------------

captcha = input("\nOpen the captcha image and enter the code: ").strip()


# ------------------------------------------------------------
# Submit PTCL bill request
# ------------------------------------------------------------

payload = hidden.copy()

payload.update(
    {
        "ctl00$ContentPlaceHolder1$txtPhoneNo": PHONE,
        "ctl00$ContentPlaceHolder1$txtAccountID": ACCOUNT_ID,
        "ctl00$ContentPlaceHolder1$txtCaptcha": captcha,
        "ctl00$ContentPlaceHolder1$btnVerify": "Search",
    }
)

print("\nSubmitting PTCL request...")

result = session.post(URL, data=payload, timeout=30)

print("Status:", result.status_code)
print("Final URL:", result.url)
print("Length:", len(result.content))

(OUT / "result.html").write_bytes(result.content)


# ------------------------------------------------------------
# Extract visible bill text
# ------------------------------------------------------------

result_soup = BeautifulSoup(result.text, "html.parser")

text = result_soup.get_text("\n", strip=True)

(OUT / "result.txt").write_text(text, encoding="utf-8")

print("\nRESULT:")
print("=" * 80)
print(text[:20000])

print("\nSaved:")
print((OUT / "result.html").resolve())
print((OUT / "result.txt").resolve())
