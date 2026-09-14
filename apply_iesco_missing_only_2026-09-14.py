from pathlib import Path
import shutil

ROOT = Path.cwd()
DASHBOARD = ROOT / "invoices/templates/invoices/iesco_bill_dashboard.html"
DETAIL = ROOT / "invoices/templates/invoices/iesco_bill_dashboard_detail.html"
VIEWS = ROOT / "invoices/views_iesco.py"

def backup(path):
    bak = path.with_suffix(path.suffix + ".pre_iesco_final.bak")
    if not bak.exists():
        shutil.copy2(path, bak)

def save(path, old, new):
    if new == old:
        print(f"[OK] {path}: already up to date")
        return
    backup(path)
    path.write_text(new, encoding="utf-8", newline="")
    print(f"[UPDATED] {path}")

def replace_once(text, old, new, label):
    if new in text:
        print(f"[OK] {label}: already applied")
        return text
    if old not in text:
        raise RuntimeError(f"{label}: anchor not found")
    return text.replace(old, new, 1)

def update_dashboard(path):
    text = path.read_text(encoding="utf-8")
    old_text = text

    close_btn = '          <button id="iescoReferenceCloseButton" type="button" class="btn btn-secondary" data-bs-dismiss="modal" disabled>Close</button>'
    download_btn = (
        '          <a id="iescoReferenceDownloadButton" class="btn btn-success d-none" '
        'href="{% url \'invoices:iesco_bill_export\' %}?batch=1" download>'
        '<i class="fas fa-download me-1"></i>Download Bills CSV</a>\n'
        + close_btn
    )
    if 'id="iescoReferenceDownloadButton"' not in text:
        text = replace_once(text, close_btn, download_btn, "dashboard download button")

    old = "    const referenceFinalMessage = document.getElementById('iescoReferenceFinalMessage');"
    new = old + "\n    const referenceDownloadButton = document.getElementById('iescoReferenceDownloadButton');"
    if "const referenceDownloadButton" not in text:
        text = replace_once(text, old, new, "dashboard JS download variable")

    old = (
        "      referenceCloseButton.disabled = true;\n"
        "      referenceRefreshButton.classList.add('d-none');"
    )
    new = (
        "      referenceCloseButton.disabled = true;\n"
        "      if (referenceDownloadButton) referenceDownloadButton.classList.add('d-none');\n"
        "      referenceRefreshButton.classList.add('d-none');"
    )
    if "referenceDownloadButton.classList.add('d-none')" not in text:
        text = replace_once(text, old, new, "dashboard JS reset")

    old = (
        "        referenceCloseButton.disabled = false;\n"
        "        referenceRefreshButton.classList.remove('d-none');"
    )
    new = (
        "        referenceCloseButton.disabled = false;\n"
        "        referenceRefreshButton.classList.remove('d-none');\n"
        "        if (succeeded > 0 && referenceDownloadButton) {\n"
        "          referenceDownloadButton.classList.remove('d-none');\n"
        "          referenceFinalMessage.insertAdjacentHTML('beforeend', "
        "'<br><span class=\"small\">A production-ready Bills CSV will download automatically. "
        "If your browser blocks it, click <strong>Download Bills CSV</strong>.</span>');\n"
        "          window.setTimeout(function () { referenceDownloadButton.click(); }, 350);\n"
        "        }"
    )
    if "A production-ready Bills CSV will download automatically" not in text:
        text = replace_once(text, old, new, "dashboard JS completion")

    save(path, old_text, text)

def update_detail(path):
    text = path.read_text(encoding="utf-8")
    old_text = text

    old_head = '<thead><tr><th>Register</th><th>Previous</th><th>Current</th><th>Units</th></tr></thead>'
    new_head = '<thead><tr><th>Register</th><th>Previous</th><th>Current</th><th>Units</th><th>Net</th></tr></thead>'
    if new_head not in text:
        text = replace_once(text, old_head, new_head, "detail register header")

    old_row = """                    <tr>
                      <td>{{ register.direction|title }} {% if register.period == "off_peak" %}OP{% else %}P{% endif %}</td>
                      <td>{{ register.previous|default:"—" }}</td>
                      <td>{{ register.present|default:"—" }}</td>
                      <td class="fw-semibold">{{ register.units|default:"—" }}</td>
                    </tr>"""
    new_row = """                    <tr>
                      <td>{{ register.direction|title }} {% if register.period == "off_peak" %}OP{% else %}P{% endif %}</td>
                      <td>{{ register.previous|default:"—" }}</td>
                      <td>{{ register.present|default:"—" }}</td>
                      <td class="fw-semibold">{{ register.units|default:"—" }}</td>
                      {% if register.direction == "import" and register.period == "off_peak" %}
                      <td rowspan="2" class="align-middle fw-bold">{{ reading.import_units_display }}</td>
                      {% elif register.direction == "export" and register.period == "off_peak" %}
                      <td rowspan="2" class="align-middle fw-bold">{{ reading.export_units_display }}</td>
                      {% endif %}
                    </tr>"""
    if 'rowspan="2" class="align-middle fw-bold">{{ reading.import_units_display }}</td>' not in text:
        text = replace_once(text, old_row, new_row, "detail register net cells")

    save(path, old_text, text)

def update_views(path):
    text = path.read_text(encoding="utf-8")
    old_text = text

    start = text.find("def export_last_csv(request):")
    end = text.find("def _formatted_register_value", start)
    if start < 0 or end < 0:
        raise RuntimeError("views export_last_csv block not found")

    block = text[start:end]

    if "batch_only =" not in block:
        old = '    payment_status = (request.GET.get("payment_status") or "").strip()\n'
        new = old + '    batch_only = (request.GET.get("batch") or "").strip() == "1"\n'
        if old not in block:
            raise RuntimeError("views batch flag anchor not found")
        block = block.replace(old, new, 1)

    if "batch_ids = [" not in block:
        old = "    readings = IescoBillReading.objects.filter(reference_no__in=visible_references)\n"
        new = old + """    if batch_only:
        batch_ids = [
            int(value)
            for value in request.session.get(EXPORT_SESSION_KEY, [])
            if str(value).isdigit()
        ]
        if not batch_ids:
            messages.error(request, "No newly fetched IESCO bills are ready for download.")
            return redirect("invoices:iesco_bill_reading_list")
        readings = readings.filter(pk__in=batch_ids)
"""
        if old not in block:
            raise RuntimeError("views batch filter anchor not found")
        block = block.replace(old, new, 1)

    if 'filename_prefix = "iesco-bills-fetched"' not in block:
        old = '    filename = f"iesco-bills-{timezone.localtime():%Y%m%d-%H%M%S}.csv"\n'
        new = (
            '    filename_prefix = "iesco-bills-fetched" if batch_only else "iesco-bills"\n'
            '    filename = f"{filename_prefix}-{timezone.localtime():%Y%m%d-%H%M%S}.csv"\n'
        )
        if old not in block:
            raise RuntimeError("views filename anchor not found")
        block = block.replace(old, new, 1)

    text = text[:start] + block + text[end:]
    save(path, old_text, text)

def main():
    for p in (DASHBOARD, DETAIL, VIEWS):
        if not p.exists():
            print(f"[ERROR] Missing file: {p}")
            return 2
    try:
        update_dashboard(DASHBOARD)
        update_detail(DETAIL)
        update_views(VIEWS)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        print("Backups use the suffix .pre_iesco_final.bak")
        return 1

    print("Update completed successfully.")
    print("Run:")
    print("  python manage.py check")
    print("  python manage.py test invoices.test_iesco_ingest --keepdb")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
