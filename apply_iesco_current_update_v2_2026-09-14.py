from __future__ import annotations

from pathlib import Path
import re
import shutil
import sys

ROOT = Path.cwd()
DASHBOARD = ROOT / "invoices/templates/invoices/iesco_bill_dashboard.html"
DETAIL = ROOT / "invoices/templates/invoices/iesco_bill_dashboard_detail.html"
VIEWS = ROOT / "invoices/views_iesco.py"


def backup(path: Path):
    b = path.with_suffix(path.suffix + ".pre_iesco_update.bak")
    if not b.exists():
        shutil.copy2(path, b)


def save(path: Path, original: str, text: str):
    if text == original:
        print(f"[OK] {path}: no changes needed")
        return
    backup(path)
    path.write_text(text, encoding="utf-8", newline="")
    print(f"[UPDATED] {path}")


def update_dashboard(path: Path):
    text = path.read_text(encoding="utf-8")
    original = text

    text = text.replace(
        ".iesco-reading-popover-grid { width: 270px; }",
        ".iesco-reading-popover-grid { width: 430px; }",
    )

    if 'id="iescoReferenceDownloadButton"' not in text:
        close_re = re.compile(
            r'(?m)^(?P<indent>\s*)<button id="iescoReferenceCloseButton"[^>]*>Close</button>\s*$'
        )
        m = close_re.search(text)
        if not m:
            raise RuntimeError("dashboard: Close button anchor not found")
        indent = m.group("indent")
        button = (
            f'{indent}<a id="iescoReferenceDownloadButton" class="btn btn-success d-none" '
            f'href="{{% url \'invoices:iesco_bill_export\' %}}?batch=1" download>'
            f'<i class="fas fa-download me-1"></i>Download Bills CSV</a>\n'
        )
        text = text[:m.start()] + button + text[m.start():]

    old_table = (
        '<thead><tr><th>Register</th><th>Previous</th><th>Current</th></tr></thead>'
        '<tbody>{% for register in reading.register_display_rows %}'
        '<tr><th>{{ register.short_label }}</th>'
        '<td>{{ register.previous|default:"—" }}</td>'
        '<td>{{ register.present|default:"—" }}</td></tr>'
        '{% endfor %}</tbody>'
    )
    new_table = (
        '<thead><tr><th>Flow</th><th>Period</th><th>Previous</th><th>Current</th><th>Net</th></tr></thead>'
        '<tbody>{% for register in reading.meter_readings %}'
        '<tr><th>{{ register.direction|title }}</th>'
        '<td>{% if register.period == "off_peak" %}Off Peak{% else %}Peak{% endif %}</td>'
        '<td>{{ register.previous|default:"—" }}</td>'
        '<td>{{ register.present|default:"—" }}</td>'
        '{% if register.direction == "import" and register.period == "off_peak" %}'
        '<td rowspan="2" class="align-middle fw-bold">{{ reading.import_units_display }}</td>'
        '{% elif register.direction == "export" and register.period == "off_peak" %}'
        '<td rowspan="2" class="align-middle fw-bold">{{ reading.export_units_display }}</td>'
        '{% endif %}</tr>{% endfor %}</tbody>'
    )
    if old_table in text:
        count = text.count(old_table)
        text = text.replace(old_table, new_table)
        print(f"[INFO] dashboard register tables updated: {count}")
    elif new_table not in text:
        table_re = re.compile(
            r'<thead>\s*<tr>\s*<th>Register</th>\s*<th>Previous</th>\s*<th>Current</th>\s*</tr>\s*</thead>\s*'
            r'<tbody>\s*{%\s*for register in reading\.register_display_rows\s*%}\s*'
            r'<tr>\s*<th>{{\s*register\.short_label\s*}}</th>\s*'
            r'<td>{{\s*register\.previous\|default:"—"\s*}}</td>\s*'
            r'<td>{{\s*register\.present\|default:"—"\s*}}</td>\s*</tr>\s*'
            r'{%\s*endfor\s*%}\s*</tbody>',
            re.S,
        )
        text, count = table_re.subn(new_table, text)
        if count == 0:
            raise RuntimeError("dashboard: register-table anchor not found")
        print(f"[INFO] dashboard register tables updated: {count}")

    if "const referenceDownloadButton" not in text:
        var_re = re.compile(
            r"(?m)^(?P<indent>\s*)const referenceFinalMessage = document\.getElementById\('iescoReferenceFinalMessage'\);\s*$"
        )
        m = var_re.search(text)
        if not m:
            raise RuntimeError("dashboard: JS final-message anchor not found")
        indent = m.group("indent")
        addition = (
            f"{m.group(0)}\n"
            f"{indent}const referenceDownloadButton = document.getElementById('iescoReferenceDownloadButton');"
        )
        text = text[:m.start()] + addition + text[m.end():]

    if "referenceDownloadButton?.classList.add('d-none')" not in text:
        reset_re = re.compile(
            r"(?m)^(?P<indent>\s*)referenceCloseButton\.disabled = true;\s*\n"
            r"(?P=indent)referenceRefreshButton\.classList\.add\('d-none'\);"
        )
        m = reset_re.search(text)
        if not m:
            raise RuntimeError("dashboard: JS reset anchor not found")
        indent = m.group("indent")
        repl = (
            f"{indent}referenceCloseButton.disabled = true;\n"
            f"{indent}referenceDownloadButton?.classList.add('d-none');\n"
            f"{indent}referenceRefreshButton.classList.add('d-none');"
        )
        text = text[:m.start()] + repl + text[m.end():]

    if "A production-ready Bills CSV will download automatically" not in text:
        finish_re = re.compile(
            r"(?m)^(?P<indent>\s*)referenceCloseButton\.disabled = false;\s*\n"
            r"(?P=indent)referenceRefreshButton\.classList\.remove\('d-none'\);"
        )
        m = finish_re.search(text)
        if not m:
            raise RuntimeError("dashboard: JS completion anchor not found")
        indent = m.group("indent")
        repl = (
            f"{indent}referenceCloseButton.disabled = false;\n"
            f"{indent}referenceRefreshButton.classList.remove('d-none');\n"
            f"{indent}if (succeeded > 0 && referenceDownloadButton) {{\n"
            f"{indent}  referenceDownloadButton.classList.remove('d-none');\n"
            f"{indent}  referenceFinalMessage.insertAdjacentHTML('beforeend', '<br><span class=\"small\">A production-ready Bills CSV will download automatically. If your browser blocks it, click <strong>Download Bills CSV</strong>.</span>');\n"
            f"{indent}  window.setTimeout(function () {{ referenceDownloadButton.click(); }}, 350);\n"
            f"{indent}}}"
        )
        text = text[:m.start()] + repl + text[m.end():]

    save(path, original, text)


def update_detail(path: Path):
    text = path.read_text(encoding="utf-8")
    original = text

    if '<th style="width: 3%;">#</th>' not in text:
        header_re = re.compile(
            r'(?P<indent>\s*)<th style="width: 8%;">Month</th>\s*'
            r'<th style="width: 17%;">Consumer / Occupancy</th>\s*'
            r'<th style="width: 13%;">Readings</th>\s*'
            r'<th style="width: 10%;">Units / Rate</th>\s*'
            r'<th style="width: 12%;">Charges</th>\s*'
            r'<th style="width: 13%;">Dates / Meter</th>\s*'
            r'<th style="width: 13%;">Detail / Trust</th>\s*'
            r'<th class="text-end" style="width: 14%;">Actions</th>',
            re.S,
        )
        m = header_re.search(text)
        if not m:
            raise RuntimeError("detail: table-header anchor not found")
        indent = m.group("indent")
        repl = (
            f'{indent}<th style="width: 3%;">#</th>\n'
            f'{indent}<th style="width: 7%;">Month</th>\n'
            f'{indent}<th style="width: 15%;">Consumer / Occupancy</th>\n'
            f'{indent}<th style="width: 14%;">Register Detail</th>\n'
            f'{indent}<th style="width: 9%;">Units / Rate</th>\n'
            f'{indent}<th style="width: 11%;">Charges</th>\n'
            f'{indent}<th style="width: 12%;">Dates / Meter</th>\n'
            f'{indent}<th style="width: 13%;">Detail / Trust</th>\n'
            f'{indent}<th class="text-end" style="width: 16%;">Actions</th>'
        )
        text = text[:m.start()] + repl + text[m.end():]

    if '<td class="text-muted">{{ forloop.counter }}</td>' not in text:
        row_re = re.compile(
            r'(?m)^(?P<indent>\s*)<tr>\s*\n(?P=indent)\s*<td>\s*\n(?P<body>\s*<span class="cell-line cell-strong">{{ reading\.bill_month }}</span>)'
        )
        m = row_re.search(text)
        if not m:
            raise RuntimeError("detail: serial-row anchor not found")
        indent = m.group("indent")
        replacement = (
            f"{indent}<tr>\n"
            f'{indent}  <td class="text-muted">{{{{ forloop.counter }}}}</td>\n'
            f"{indent}  <td>\n"
            f"{m.group('body')}"
        )
        text = text[:m.start()] + replacement + text[m.end():]

    if '<th>Flow</th><th>Period</th><th>Prev</th><th>Curr</th><th>Net</th>' not in text:
        start_marker = '            <td>\n              {% if reading.register_display_rows %}'
        start = text.find(start_marker)
        if start < 0:
            raise RuntimeError("detail: register-detail start anchor not found")
        end_marker = '            </td>\n            <td>\n              {% if reading.has_export_registers %}'
        end = text.find(end_marker, start)
        if end < 0:
            raise RuntimeError("detail: register-detail end anchor not found")
        new_block = '''            <td>
              {% if reading.meter_readings %}
                <table class="table table-sm table-bordered mb-0">
                  <thead><tr><th>Flow</th><th>Period</th><th>Prev</th><th>Curr</th><th>Net</th></tr></thead>
                  <tbody>{% for register in reading.meter_readings %}<tr><td>{{ register.direction|title }}</td><td>{% if register.period == "off_peak" %}OP{% else %}P{% endif %}</td><td>{{ register.previous|default:"—" }}</td><td>{{ register.present|default:"—" }}</td>{% if register.direction == "import" and register.period == "off_peak" %}<td rowspan="2" class="align-middle fw-bold">{{ reading.import_units_display }}</td>{% elif register.direction == "export" and register.period == "off_peak" %}<td rowspan="2" class="align-middle fw-bold">{{ reading.export_units_display }}</td>{% endif %}</tr>{% endfor %}</tbody>
                </table>
              {% elif reading.register_display_rows %}
                {% for register in reading.register_display_rows %}<span class="cell-line"><span class="cell-muted">{{ register.short_label }}</span> {{ register.previous|default:"—" }} → {{ register.present|default:"—" }}</span>{% endfor %}
              {% else %}<span class="cell-line">—</span>{% endif %}
            </td>
'''
        text = text[:start] + new_block + text[end + len('            </td>\n'):]

    save(path, original, text)


def update_views(path: Path):
    text = path.read_text(encoding="utf-8")
    original = text

    start = text.find("def export_last_csv(request):")
    end = text.find("def _formatted_register_value", start)
    if start < 0 or end < 0:
        raise RuntimeError("views: export_last_csv block not found")
    block = text[start:end]

    if "batch_only =" not in block:
        anchor = '    payment_status = (request.GET.get("payment_status") or "").strip()\n'
        if anchor not in block:
            raise RuntimeError("views: batch flag anchor not found")
        block = block.replace(
            anchor,
            anchor + '    batch_only = (request.GET.get("batch") or "").strip() == "1"\n',
            1,
        )

    if "batch_ids = [" not in block:
        anchor = "    readings = IescoBillReading.objects.filter(reference_no__in=visible_references)\n"
        if anchor not in block:
            raise RuntimeError("views: batch filtering anchor not found")
        block = block.replace(
            anchor,
            anchor
            + '''    if batch_only:
        batch_ids = [
            int(value)
            for value in request.session.get(EXPORT_SESSION_KEY, [])
            if str(value).isdigit()
        ]
        if not batch_ids:
            messages.error(request, "No newly fetched IESCO bills are ready for download.")
            return redirect("invoices:iesco_bill_reading_list")
        readings = readings.filter(pk__in=batch_ids)
''',
            1,
        )

    if 'filename_prefix = "iesco-bills-fetched"' not in block:
        anchor = '    filename = f"iesco-bills-{timezone.localtime():%Y%m%d-%H%M%S}.csv"\n'
        if anchor not in block:
            raise RuntimeError("views: export filename anchor not found")
        block = block.replace(
            anchor,
            '    filename_prefix = "iesco-bills-fetched" if batch_only else "iesco-bills"\n'
            '    filename = f"{filename_prefix}-{timezone.localtime():%Y%m%d-%H%M%S}.csv"\n',
            1,
        )

    text = text[:start] + block + text[end:]
    save(path, original, text)


def main():
    for p in (DASHBOARD, DETAIL, VIEWS):
        if not p.exists():
            print(f"[ERROR] Missing: {p}")
            return 2
    try:
        update_dashboard(DASHBOARD)
        update_detail(DETAIL)
        update_views(VIEWS)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        print("Backups use .pre_iesco_update.bak")
        return 1
    print("Update completed successfully.")
    print("Now run:")
    print("  python manage.py check")
    print("  python manage.py test invoices.test_iesco_ingest --keepdb")
    return 0


if __name__ == "__main__":
    sys.exit(main())
