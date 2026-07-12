from io import BytesIO

from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

from leases.models import AgreementSignatureTemplate, LeaseDocument
from leases.utils import do_replace_placeholders


def _pdf(html, request):
    return HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()


def _period(lease, history=None):
    return (
        getattr(history, "start_date", None) or lease.start_date,
        getattr(history, "end_date", None) or lease.end_date,
    )


def _relationship_name(value):
    return str(value) if value else ""


def party_snapshot(lease, history=None):
    def data(person, relationship=""):
        return {
            "name": person.get_full_name() if person else "",
            "cnic": (getattr(person, "cnic", "") or "") if person else "",
            "phone": (getattr(person, "phone", "") or "") if person else "",
            "relationship": _relationship_name(relationship),
        }

    witness1 = getattr(history, "witness1_tenant", None) if history else lease.witness1_tenant
    witness2 = getattr(history, "witness2_tenant", None) if history else lease.witness2_tenant
    occupants = [{
        "name": row.family_member.get_full_name(),
        "cnic": row.family_member.cnic or "",
        "relationship": str(row.relationship_type or row.relationship or ""),
    } for row in lease.family_members.select_related("family_member", "relationship_type").filter(lives_with_tenant=True)]

    return {
        "authorized_occupants": occupants,
        "proposer": data(lease.proposer, lease.proposer_relationship),
        "seconder": data(lease.seconder, lease.seconder_relationship),
        "witness1": data(witness1),
        "witness2": data(witness2),
    }


def _declaration_values(lease, history, parties):
    start_date, end_date = _period(lease, history)
    values = {
        "lease_number": str(lease.pk),
        "tenant_name": lease.tenant.get_full_name() or "________________",
        "tenant_cnic": lease.tenant.cnic or "________________",
        "property_unit": f"{lease.unit.property} / {lease.unit}",
        "lease_start_date": start_date.strftime("%B %d, %Y") if start_date else "________________",
        "lease_end_date": end_date.strftime("%B %d, %Y") if end_date else "________________",
    }
    for role in ("proposer", "seconder"):
        row = parties[role]
        values.update({
            f"{role}_name": row["name"] or "________________",
            f"{role}_cnic": row["cnic"] or "________________",
            f"{role}_phone": row["phone"] or "________________",
            f"{role}_relationship": row["relationship"] or "________________",
        })
    return values


def _render_template_text(text, values):
    rendered = text or ""
    for key, value in values.items():
        rendered = rendered.replace("{{ " + key + " }}", str(value))
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered


def agreement_pdf(request, lease, history, clauses):
    for clause in clauses:
        clause.rendered_text = do_replace_placeholders(clause.template_text, lease)
    html = render_to_string(
        "leases/agreement_preview.html",
        {
            "lease": lease,
            "history": history,
            "clauses": clauses,
            "agreement_date": getattr(history, "agreement_date", None)
            or getattr(history, "start_date", lease.start_date),
        },
        request=request,
    )
    return _pdf(html, request)


def _inspection_template_name_for_lease(lease):
    unit = lease.unit
    bedrooms = getattr(unit, "bedrooms", None)
    if bedrooms is not None:
        try:
            return "Room" if int(bedrooms) <= 1 else "Apartment Standard"
        except (TypeError, ValueError):
            pass
    type_text = " ".join(str(value or "") for value in (
        getattr(unit, "unit_type", ""), getattr(unit, "type", ""),
        getattr(unit.property, "type", ""), getattr(unit.property, "property_type", ""),
    )).lower()
    return "Room" if "single room" in type_text or type_text.strip() == "room" else "Apartment Standard"


def _create_default_move_in_inspection(request, lease):
    from leases.models_inspections import InspectionTemplate, InspectionType, LeaseInspection

    inspection_type = InspectionType.objects.filter(name__iexact="Move In", active=True).first()
    if inspection_type is None:
        raise RuntimeError("Active inspection type 'Move In' was not found.")
    template_name = _inspection_template_name_for_lease(lease)
    inspection_template = InspectionTemplate.objects.filter(name__iexact=template_name, active=True).first()
    if inspection_template is None:
        raise RuntimeError(f"Active inspection template '{template_name}' was not found.")
    user = request.user if getattr(request.user, "is_authenticated", False) else None
    inspection = LeaseInspection.objects.create(
        lease=lease, property=lease.unit.property, unit=lease.unit, tenant=lease.tenant,
        inspection_type=inspection_type, inspection_template=inspection_template,
        inspection_date=timezone.localdate(), inspector=None, inspector_name="",
        status=LeaseInspection.STATUS_DRAFT, created_by=user,
    )
    inspection.snapshot_template_items()
    inspection.add_audit("created automatically for agreement package", user, {
        "template": inspection_template.name, "type": inspection_type.name,
    })
    return inspection


def get_or_create_inspection(request, lease):
    inspection = (
        lease.inspections.select_related(
            "inspection_type", "inspection_template", "property", "unit", "tenant",
            "inspector", "approved_by", "created_by",
        )
        .prefetch_related("details__photos", "meter_readings", "keys", "appliances", "damage_charges__invoice")
        .order_by("-inspection_date", "-id")
        .first()
    )
    return inspection or _create_default_move_in_inspection(request, lease)


def inspection_pdf(request, lease):
    from leases.views_inspections import _inspection_pdf_bytes
    return _inspection_pdf_bytes(request, get_or_create_inspection(request, lease))


def police_context(lease):
    return {
        "lease": lease, "tenant": lease.tenant, "property": lease.unit.property,
        "unit": lease.unit,
        "family_members": lease.family_members.select_related("family_member", "relationship_type"),
        "vehicles": lease.vehicles.filter(is_active=True).select_related("vehicle_type"),
        "generated_at": timezone.localtime(),
    }


def police_pdf(request, lease):
    return _pdf(render_to_string("leases/police_verification_summary_pdf.html", police_context(lease), request=request), request)


def signature_context(lease, history=None, snapshot=None):
    parties = snapshot or party_snapshot(lease, history)
    config = AgreementSignatureTemplate.current()
    values = _declaration_values(lease, history, parties)
    return {
        "lease": lease, "history": history, "parties": parties,
        "signature_config": config,
        "proposer_declaration": _render_template_text(config.proposer_declaration, values),
        "seconder_declaration": _render_template_text(config.seconder_declaration, values),
        "generated_at": timezone.now(),
    }


def signature_pdf(request, lease, history=None, snapshot=None):
    return _pdf(render_to_string("leases/agreement_signature_page.html", signature_context(lease, history, snapshot), request=request), request)


def merge_pdfs(parts):
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        from PyPDF2 import PdfReader, PdfWriter
    writer = PdfWriter()
    for part in parts:
        reader = PdfReader(BytesIO(part))
        for page in reader.pages:
            writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _package_basename(lease, history):
    suffix = f"-renewal-{history.renewal_number}" if history and not history.is_original else ""
    return f"lease-{lease.pk}{suffix}-agreement-package"


def _save_document(request, lease, history, filename, content):
    doc = LeaseDocument(
        lease=lease, lease_history=history,
        category="lease_renewal_agreement" if history and not history.is_original else "lease_agreement",
        display_name=filename, original_filename=filename,
        uploaded_by=request.user if request.user.is_authenticated else None,
    )
    doc.file.save(filename, ContentFile(content), save=True)
    return doc


def build_package(request, lease, history, clauses):
    components = []
    try:
        components.append(agreement_pdf(request, lease, history, clauses))
    except Exception as exc:
        raise RuntimeError(f"Agreement PDF generation failed: {exc}") from exc
    try:
        components.append(inspection_pdf(request, lease))
    except Exception as exc:
        raise RuntimeError(f"Agreement generated, but inspection PDF generation failed: {exc}") from exc
    try:
        components.append(police_pdf(request, lease))
    except Exception as exc:
        raise RuntimeError(f"Agreement and inspection generated, but police report generation failed: {exc}") from exc
    try:
        components.append(signature_pdf(request, lease, history))
    except Exception as exc:
        raise RuntimeError(f"Signature page generation failed: {exc}") from exc
    merged = merge_pdfs(components)
    filename = _package_basename(lease, history) + ".pdf"
    return merged, filename, _save_document(request, lease, history, filename, merged)


# ----------------------------- DOCX package -----------------------------

def _set_cell_text(cell, text, bold=False, size=8):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = 0
    run = p.add_run(str(text or ""))
    run.bold = bold
    from docx.shared import Pt
    run.font.size = Pt(size)


def _add_heading(doc, text, size=14):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(5)
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(size)


def _add_inspection_docx(doc, inspection):
    from docx.shared import Pt
    doc.add_page_break()
    _add_heading(doc, "Inspection Sheet")
    meta = doc.add_table(rows=2, cols=4)
    meta.style = "Table Grid"
    values = [
        ("Lease", f"#{inspection.lease_id}"), ("Type", str(inspection.inspection_type)),
        ("Property / Unit", f"{inspection.property} / {inspection.unit}"),
        ("Date", inspection.inspection_date.strftime("%B %d, %Y")),
    ]
    for idx, (label, value) in enumerate(values):
        row, col = divmod(idx, 2)
        _set_cell_text(meta.cell(row, col * 2), label, True)
        _set_cell_text(meta.cell(row, col * 2 + 1), value)
    table = doc.add_table(rows=1, cols=6)
    table.style = "Table Grid"
    for i, text in enumerate(("SN", "Category", "Item", "Qty", "Condition", "Remarks")):
        _set_cell_text(table.rows[0].cells[i], text, True)
    for idx, item in enumerate(inspection.details.all(), 1):
        cells = table.add_row().cells
        vals = (idx, item.category, item.item_name, item.quantity, item.status_name, item.remarks)
        for i, value in enumerate(vals):
            _set_cell_text(cells[i], value)
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.add_run("Inspector: ____________________    Tenant Signature: ____________________    Date: ____________________")


def _add_police_docx(doc, lease):
    doc.add_page_break()
    _add_heading(doc, "Police Verification Report")
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    rows = [
        ("Tenant", lease.tenant.get_full_name()), ("Tenant CNIC", lease.tenant.cnic or ""),
        ("Phone", lease.tenant.phone or ""), ("Property / Unit", f"{lease.unit.property} / {lease.unit}"),
        ("Lease Period", f"{lease.start_date} to {lease.end_date}"),
        ("Address", lease.tenant.address or ""),
    ]
    for label, value in rows:
        cells = table.add_row().cells
        _set_cell_text(cells[0], label, True, 9)
        _set_cell_text(cells[1], value, False, 9)
    doc.add_paragraph("Family Members", style=None).runs[0].bold = True
    fam = doc.add_table(rows=1, cols=4); fam.style = "Table Grid"
    for i, text in enumerate(("SN", "Name", "CNIC", "Relationship")):
        _set_cell_text(fam.rows[0].cells[i], text, True)
    for idx, row in enumerate(lease.family_members.select_related("family_member", "relationship_type"), 1):
        cells = fam.add_row().cells
        vals = (idx, row.family_member.get_full_name(), row.family_member.cnic or "", str(row.relationship_type or row.relationship or ""))
        for i, value in enumerate(vals): _set_cell_text(cells[i], value)
    doc.add_paragraph("Verification Remarks: ______________________________________________________________")
    doc.add_paragraph("Police Station / Officer: __________________________    Date: __________________________")


def _add_signature_docx(doc, context):
    from docx.shared import Inches, Pt
    doc.add_page_break()
    _add_heading(doc, context["signature_config"].heading or "Proposer and Seconder Declaration", 13)
    for role, title, declaration in (
        ("proposer", "Proposer Declaration", context["proposer_declaration"]),
        ("seconder", "Seconder Declaration", context["seconder_declaration"]),
    ):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(title); r.bold = True; r.font.size = Pt(10)
        p = doc.add_paragraph(declaration)
        p.paragraph_format.space_after = Pt(3)
        p.paragraph_format.line_spacing = 1.0
        for run in p.runs: run.font.size = Pt(8.5)
        party = context["parties"][role]
        table = doc.add_table(rows=2, cols=4); table.style = "Table Grid"
        labels = ("Full Name", "CNIC", "Phone Number", "Relationship to Tenant")
        vals = (party["name"] or "________________", party["cnic"] or "________________", party["phone"] or "________________", party["relationship"] or "________________")
        for i, label in enumerate(labels): _set_cell_text(table.cell(0, i), label, True, 7.5)
        for i, value in enumerate(vals): _set_cell_text(table.cell(1, i), value, False, 8)
        sig = doc.add_paragraph("Signature: __________________________    Date: __________________________")
        sig.paragraph_format.space_after = Pt(2)
        if context["signature_config"].show_thumb_impression:
            doc.add_paragraph("Thumb Impression: __________________________")
    # Witnesses remain simple signature fields; there is deliberately no witness declaration.
    witnesses = doc.add_table(rows=2, cols=2); witnesses.style = "Table Grid"
    for col, role in enumerate(("witness1", "witness2")):
        party = context["parties"][role]
        _set_cell_text(witnesses.cell(0, col), f"{role.title().replace('Witness', 'Witness ')}: {party['name'] or '________________'}\nCNIC: {party['cnic'] or '________________'}", False, 8)
        _set_cell_text(witnesses.cell(1, col), "Signature: ____________________    Date: ____________________", False, 8)


def build_docx_package(request, lease, history, clauses):
    # Reuse the current agreement converter, then append the three package components.
    for clause in clauses:
        clause.rendered_text = do_replace_placeholders(clause.template_text, lease)
    html = render_to_string("leases/agreement_preview.html", {
        "lease": lease, "history": history, "clauses": clauses,
        "agreement_date": getattr(history, "agreement_date", None) or getattr(history, "start_date", lease.start_date),
    }, request=request)
    from leases.views import html_to_docx_bytes
    from docx import Document
    try:
        agreement_bytes = html_to_docx_bytes(html, lease)
        doc = Document(BytesIO(agreement_bytes))
    except Exception as exc:
        raise RuntimeError(f"Agreement Word generation failed: {exc}") from exc
    try:
        _add_inspection_docx(doc, get_or_create_inspection(request, lease))
    except Exception as exc:
        raise RuntimeError(f"Agreement generated, but inspection Word section failed: {exc}") from exc
    try:
        _add_police_docx(doc, lease)
    except Exception as exc:
        raise RuntimeError(f"Agreement and inspection generated, but police report Word section failed: {exc}") from exc
    try:
        _add_signature_docx(doc, signature_context(lease, history))
    except Exception as exc:
        raise RuntimeError(f"Proposer/seconder Word declaration generation failed: {exc}") from exc
    out = BytesIO(); doc.save(out); content = out.getvalue()
    filename = _package_basename(lease, history) + ".docx"
    return content, filename, _save_document(request, lease, history, filename, content)
