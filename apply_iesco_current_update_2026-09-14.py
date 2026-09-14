from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
DASHBOARD = ROOT / 'invoices/templates/invoices/iesco_bill_dashboard.html'
DETAIL = ROOT / 'invoices/templates/invoices/iesco_bill_dashboard_detail.html'
VIEWS = ROOT / 'invoices/views_iesco.py'


def backup(path: Path):
    b = path.with_suffix(path.suffix + '.pre_iesco_update.bak')
    if not b.exists():
        shutil.copy2(path, b)
    return b


def write_if_changed(path: Path, old_text: str, new_text: str):
    if new_text != old_text:
        backup(path)
        path.write_text(new_text, encoding='utf-8', newline='')
        print(f'[UPDATED] {path}')
    else:
        print(f'[OK] {path}: no changes needed')


def update_dashboard(path: Path):
    text = path.read_text(encoding='utf-8')
    original = text

    text = text.replace('.iesco-reading-popover-grid { width: 270px; }',
                        '.iesco-reading-popover-grid { width: 430px; }')

    close_btn = '          <button id="iescoReferenceCloseButton" type="button" class="btn btn-secondary" data-bs-dismiss="modal" disabled>Close</button>\n'
    download_btn = '          <a id="iescoReferenceDownloadButton" class="btn btn-success d-none" href="{% url \'invoices:iesco_bill_export\' %}?batch=1" download><i class="fas fa-download me-1"></i>Download Bills CSV</a>\n'
    if 'id="iescoReferenceDownloadButton"' not in text:
        if close_btn not in text:
            raise RuntimeError('dashboard: Close button anchor not found')
        text = text.replace(close_btn, download_btn + close_btn, 1)

    old_register = '<thead><tr><th>Register</th><th>Previous</th><th>Current</th></tr></thead><tbody>{% for register in reading.register_display_rows %}<tr><th>{{ register.short_label }}</th><td>{{ register.previous|default:"—" }}</td><td>{{ register.present|default:"—" }}</td></tr>{% endfor %}</tbody>'
    new_register = '<thead><tr><th>Flow</th><th>Period</th><th>Previous</th><th>Current</th><th>Net</th></tr></thead><tbody>{% for register in reading.meter_readings %}<tr><th>{{ register.direction|title }}</th><td>{% if register.period == "off_peak" %}Off Peak{% else %}Peak{% endif %}</td><td>{{ register.previous|default:"—" }}</td><td>{{ register.present|default:"—" }}</td>{% if register.direction == "import" and register.period == "off_peak" %}<td rowspan="2" class="align-middle fw-bold">{{ reading.import_units_display }}</td>{% elif register.direction == "export" and register.period == "off_peak" %}<td rowspan="2" class="align-middle fw-bold">{{ reading.export_units_display }}</td>{% endif %}</tr>{% endfor %}</tbody>'
    if old_register in text:
        text = text.replace(old_register, new_register)
    elif new_register not in text:
        raise RuntimeError('dashboard: register-table anchor not found')

    old_var = "    const referenceFinalMessage = document.getElementById('iescoReferenceFinalMessage');\n"
    new_var = old_var + "    const referenceDownloadButton = document.getElementById('iescoReferenceDownloadButton');\n"
    if 'const referenceDownloadButton' not in text:
        if old_var not in text:
            raise RuntimeError('dashboard: JS final-message anchor not found')
        text = text.replace(old_var, new_var, 1)

    old_reset = "      referenceCloseButton.disabled = true;\n      referenceRefreshButton.classList.add('d-none');"
    new_reset = "      referenceCloseButton.disabled = true;\n      referenceDownloadButton?.classList.add('d-none');\n      referenceRefreshButton.classList.add('d-none');"
    if "referenceDownloadButton?.classList.add('d-none')" not in text:
        if old_reset not in text:
            raise RuntimeError('dashboard: JS reset anchor not found')
        text = text.replace(old_reset, new_reset, 1)

    old_finish = "        referenceCloseButton.disabled = false;\n        referenceRefreshButton.classList.remove('d-none');"
    new_finish = """        referenceCloseButton.disabled = false;\n        referenceRefreshButton.classList.remove('d-none');\n        if (succeeded > 0 && referenceDownloadButton) {\n          referenceDownloadButton.classList.remove('d-none');\n          referenceFinalMessage.insertAdjacentHTML('beforeend', '<br><span class=\"small\">A production-ready Bills CSV will download automatically. If your browser blocks it, click <strong>Download Bills CSV</strong>.</span>');\n          window.setTimeout(function () { referenceDownloadButton.click(); }, 350);\n        }"""
    if 'A production-ready Bills CSV will download automatically' not in text:
        if old_finish not in text:
            raise RuntimeError('dashboard: JS completion anchor not found')
        text = text.replace(old_finish, new_finish, 1)

    write_if_changed(path, original, text)


def update_detail(path: Path):
    text = path.read_text(encoding='utf-8')
    original = text

    old_header = '''            <th style="width: 8%;">Month</th>\n            <th style="width: 17%;">Consumer / Occupancy</th>\n            <th style="width: 13%;">Readings</th>\n            <th style="width: 10%;">Units / Rate</th>\n            <th style="width: 12%;">Charges</th>\n            <th style="width: 13%;">Dates / Meter</th>\n            <th style="width: 13%;">Detail / Trust</th>\n            <th class="text-end" style="width: 14%;">Actions</th>'''
    new_header = '''            <th style="width: 3%;">#</th>\n            <th style="width: 7%;">Month</th>\n            <th style="width: 15%;">Consumer / Occupancy</th>\n            <th style="width: 14%;">Register Detail</th>\n            <th style="width: 9%;">Units / Rate</th>\n            <th style="width: 11%;">Charges</th>\n            <th style="width: 12%;">Dates / Meter</th>\n            <th style="width: 13%;">Detail / Trust</th>\n            <th class="text-end" style="width: 16%;">Actions</th>'''
    if new_header not in text:
        if old_header not in text:
            raise RuntimeError('detail: table-header anchor not found')
        text = text.replace(old_header, new_header, 1)

    old_row = '          <tr>\n            <td>\n              <span class="cell-line cell-strong">{{ reading.bill_month }}</span>'
    new_row = '          <tr>\n            <td class="text-muted">{{ forloop.counter }}</td>\n            <td>\n              <span class="cell-line cell-strong">{{ reading.bill_month }}</span>'
    if '<td class="text-muted">{{ forloop.counter }}</td>' not in text:
        if old_row not in text:
            raise RuntimeError('detail: serial-row anchor not found')
        text = text.replace(old_row, new_row, 1)

    old_block = '''            <td>\n              {% if reading.register_display_rows %}\n                {% for register in reading.register_display_rows %}\n                  <span class="cell-line"><span class="cell-muted">{{ register.short_label }}</span> {{ register.previous|default:"—" }} → {{ register.present|default:"—" }}</span>\n                {% endfor %}\n              {% else %}\n                <span class="cell-line">—</span>\n              {% endif %}\n              {% if reading.meter_readings %}\n                <details class="mt-1">\n                  <summary class="small">Register details</summary>\n                  <div class="table-responsive mt-1">\n                    <table class="table table-sm table-bordered mb-0">\n                      <thead><tr><th>Flow</th><th>Period</th><th>Meter</th><th class="text-end">Previous</th><th class="text-end">Present</th><th class="text-end">Units</th></tr></thead>\n                      <tbody>{% for register in reading.meter_readings %}<tr><td>{{ register.direction|title }}</td><td>{% if register.period == "off_peak" %}Off Peak{% else %}Peak{% endif %}</td><td>{{ register.meter_no|default:"—" }}</td><td class="text-end">{{ register.previous|default:"—" }}</td><td class="text-end">{{ register.present|default:"—" }}</td><td class="text-end fw-semibold">{{ register.units|default:"—" }}</td></tr>{% endfor %}</tbody>\n                    </table>\n                  </div>\n                </details>\n              {% endif %}\n            </td>'''
    new_block = '''            <td>\n              {% if reading.meter_readings %}\n                <table class="table table-sm table-bordered mb-0">\n                  <thead><tr><th>Flow</th><th>Period</th><th>Prev</th><th>Curr</th><th>Net</th></tr></thead>\n                  <tbody>{% for register in reading.meter_readings %}<tr><td>{{ register.direction|title }}</td><td>{% if register.period == "off_peak" %}OP{% else %}P{% endif %}</td><td>{{ register.previous|default:"—" }}</td><td>{{ register.present|default:"—" }}</td>{% if register.direction == "import" and register.period == "off_peak" %}<td rowspan="2" class="align-middle fw-bold">{{ reading.import_units_display }}</td>{% elif register.direction == "export" and register.period == "off_peak" %}<td rowspan="2" class="align-middle fw-bold">{{ reading.export_units_display }}</td>{% endif %}</tr>{% endfor %}</tbody>\n                </table>\n              {% elif reading.register_display_rows %}\n                {% for register in reading.register_display_rows %}<span class="cell-line"><span class="cell-muted">{{ register.short_label }}</span> {{ register.previous|default:"—" }} → {{ register.present|default:"—" }}</span>{% endfor %}\n              {% else %}<span class="cell-line">—</span>{% endif %}\n            </td>'''
    if '<th>Flow</th><th>Period</th><th>Prev</th><th>Curr</th><th>Net</th>' not in text:
        if old_block not in text:
            raise RuntimeError('detail: register-detail block anchor not found')
        text = text.replace(old_block, new_block, 1)

    write_if_changed(path, original, text)


def update_views(path: Path):
    text = path.read_text(encoding='utf-8')
    original = text
    start = text.find('def export_last_csv(request):')
    end = text.find('def _formatted_register_value', start)
    if start < 0 or end < 0:
        raise RuntimeError('views: export_last_csv block not found')
    block = text[start:end]

    if 'batch_only = ' not in block:
        old = '    payment_status = (request.GET.get("payment_status") or "").strip()\n'
        new = old + '    batch_only = (request.GET.get("batch") or "").strip() == "1"\n'
        if old not in block:
            raise RuntimeError('views: batch flag anchor not found')
        block = block.replace(old, new, 1)

    if 'batch_ids = [' not in block:
        old = '    readings = IescoBillReading.objects.filter(reference_no__in=visible_references)\n'
        new = '''    readings = IescoBillReading.objects.filter(reference_no__in=visible_references)\n    if batch_only:\n        batch_ids = [\n            int(value)\n            for value in request.session.get(EXPORT_SESSION_KEY, [])\n            if str(value).isdigit()\n        ]\n        if not batch_ids:\n            messages.error(request, "No newly fetched IESCO bills are ready for download.")\n            return redirect("invoices:iesco_bill_reading_list")\n        readings = readings.filter(pk__in=batch_ids)\n'''
        if old not in block:
            raise RuntimeError('views: batch filtering anchor not found')
        block = block.replace(old, new, 1)

    if 'filename_prefix = "iesco-bills-fetched"' not in block:
        old = '    filename = f"iesco-bills-{timezone.localtime():%Y%m%d-%H%M%S}.csv"\n'
        new = '    filename_prefix = "iesco-bills-fetched" if batch_only else "iesco-bills"\n    filename = f"{filename_prefix}-{timezone.localtime():%Y%m%d-%H%M%S}.csv"\n'
        if old not in block:
            raise RuntimeError('views: export filename anchor not found')
        block = block.replace(old, new, 1)

    text = text[:start] + block + text[end:]
    write_if_changed(path, original, text)


def main():
    for p in (DASHBOARD, DETAIL, VIEWS):
        if not p.exists():
            print(f'[ERROR] Missing: {p}')
            return 2
    try:
        update_dashboard(DASHBOARD)
        update_detail(DETAIL)
        update_views(VIEWS)
    except Exception as exc:
        print(f'[ERROR] {exc}')
        print('Backups (for any files changed before the error) use .pre_iesco_update.bak')
        return 1
    print('Update completed successfully.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
