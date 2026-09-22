import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

URL = "https://ptcl.com.pk/customer/publicbill_payment"

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

s = requests.Session()
s.headers.update(headers)

r = s.get(URL, timeout=40)
r.raise_for_status()

print("STATUS:", r.status_code)
print("FINAL URL:", r.url)

soup = BeautifulSoup(r.text, "html.parser")

print("\nFORMS")
print("=" * 80)
for i, form in enumerate(soup.find_all("form"), 1):
    print(f"\nFORM {i}")
    print("action:", form.get("action"))
    print("method:", form.get("method"))
    for field in form.find_all(["input", "select", "button"]):
        print(
            field.name,
            "name=", field.get("name"),
            "id=", field.get("id"),
            "value=", field.get("value"),
            "type=", field.get("type"),
        )

print("\nSCRIPT SOURCES")
print("=" * 80)
for script in soup.find_all("script"):
    src = script.get("src")
    if src:
        print(urljoin(URL, src))

print("\nINLINE SCRIPT MATCHES")
print("=" * 80)
keywords = [
    "CaptchaInputText",
    "AccID",
    "Telephone",
    "Areacode",
    "publicbill_payment",
    "ajax",
    "$.post",
    "$.ajax",
    "fetch(",
    "url:",
    "InvoiceNo",
]

for script in soup.find_all("script"):
    txt = script.string or script.get_text("\n")
    if not txt:
        continue
    if any(k.lower() in txt.lower() for k in keywords):
        for line in txt.splitlines():
            if any(k.lower() in line.lower() for k in keywords):
                print(line.strip())

out = Path("ptcl_current_page_debug.html")
out.write_text(r.text, encoding="utf-8")
print("\nSaved:", out.resolve())
