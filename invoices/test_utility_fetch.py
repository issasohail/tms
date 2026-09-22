from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path("utility_fetch_test")
OUT.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def inspect_response(name, response):
    print("\n" + "=" * 80)
    print(name)
    print("=" * 80)
    print("Status:", response.status_code)
    print("Final URL:", response.url)
    print("Content-Type:", response.headers.get("content-type"))
    print("Content length:", len(response.content))

    raw_file = OUT / f"{name.lower()}_raw.html"
    raw_file.write_bytes(response.content)
    print("Saved raw response:", raw_file.resolve())

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        print("\nPAGE TITLE:")
        print(soup.title.get_text(" ", strip=True) if soup.title else "(none)")

        print("\nVISIBLE TEXT:")
        print("-" * 80)

        text = soup.get_text("\n", strip=True)

        # Keep output manageable but large enough to inspect bill fields.
        print(text[:15000])

        text_file = OUT / f"{name.lower()}_text.txt"
        text_file.write_text(text, encoding="utf-8")
        print("\nFull extracted text saved:", text_file.resolve())

        print("\nHTML TABLES:")
        print("-" * 80)

        tables = soup.find_all("table")
        print("Number of tables:", len(tables))

        for i, table in enumerate(tables[:20], 1):
            print(f"\n--- TABLE {i} ---")

            for row in table.find_all("tr"):
                cells = [
                    c.get_text(" ", strip=True) for c in row.find_all(["th", "td"])
                ]

                if cells:
                    print(" | ".join(cells))

        print("\nFORM FIELDS:")
        print("-" * 80)

        for tag in soup.find_all(["input", "select", "textarea"]):
            print(
                tag.name,
                "name=",
                tag.get("name"),
                "id=",
                tag.get("id"),
                "value=",
                tag.get("value"),
            )

    except Exception as exc:
        print("HTML parsing error:", exc)


session = requests.Session()
session.headers.update(HEADERS)


# =====================================================================
# SNGPL
# =====================================================================

SNGPL_CONSUMER = "81438226003"

sngpl_url = (
    "https://www.sngpl.com.pk/viewbill"
    "?artcl=artuyh709123465"
    "&cats=ct456712337"
    "&client=ANDROID"
    f"&consumer={SNGPL_CONSUMER}"
    "&contype=NewCon"
    "&mdids=85"
    "&pgname=PAGES_NAME"
    "&proc=viewbill"
    "&secs=ss7xa852op845"
)

print("\nFetching SNGPL...")

try:
    r = session.get(sngpl_url, timeout=30)
    inspect_response("SNGPL", r)
except Exception as exc:
    print("SNGPL ERROR:", repr(exc))


# =====================================================================
# PTCL
# =====================================================================

PTCL_ACCOUNT = "100001354664"
PTCL_PHONE = "0515921709"

print("\n\nTrying PTCL...")
print("Account:", PTCL_ACCOUNT)
print("Phone:", PTCL_PHONE)

# We first test the likely public PTCL duplicate-bill URLs.
ptcl_urls = [
    (
        "PTCL_1",
        "https://ptcl.com.pk/customer/publicbill_payment",
    ),
    (
        "PTCL_2",
        "https://ptcl.com.pk/Home/PageDetail?ItemId=86&linkId=507",
    ),
    (
        "PTCL_3",
        "https://dbill.ptcl.net.pk/PTCLSearchInvoice.aspx",
    ),
]

for name, url in ptcl_urls:
    print("\nTrying:", url)

    try:
        r = session.get(url, timeout=30, allow_redirects=True)
        inspect_response(name, r)

    except Exception as exc:
        print(name, "ERROR:", repr(exc))


print("\n" + "=" * 80)
print("DONE")
print("=" * 80)
print("Results saved in:")
print(OUT.resolve())
