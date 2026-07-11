from io import BytesIO
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML
from leases.models import LeaseDocument
from leases.utils import do_replace_placeholders


def _pdf(html, request): return HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()

def party_snapshot(lease, history=None):
    def data(person, fallback_name="", fallback_cnic=""):
        return {"name": person.get_full_name() if person else fallback_name or "", "cnic": person.cnic if person else fallback_cnic or "", "phone": person.phone if person else "", "address": person.address if person else ""}
    return {"authorized_occupants":[{"name":x.family_member.get_full_name(),"cnic":x.family_member.cnic or "","relationship":x.relation or ""} for x in lease.family_members.select_related("family_member","relationship_type").filter(lives_with_tenant=True)], "proposer":data(lease.proposer), "seconder":data(lease.seconder), "witness1":data(getattr(history,"witness1_tenant",None) if history else lease.witness1_tenant,getattr(history,"witness1_name","") if history else lease.witness1_name,getattr(history,"witness1_cnic","") if history else lease.witness1_cnic), "witness2":data(getattr(history,"witness2_tenant",None) if history else lease.witness2_tenant,getattr(history,"witness2_name","") if history else lease.witness2_name,getattr(history,"witness2_cnic","") if history else lease.witness2_cnic)}

def agreement_pdf(request, lease, history, clauses):
    for c in clauses: c.rendered_text=do_replace_placeholders(c.template_text,lease)
    return _pdf(render_to_string("leases/agreement_preview.html",{"lease":lease,"history":history,"clauses":clauses,"agreement_date":getattr(history,"agreement_date",None) or getattr(history,"start_date",lease.start_date)},request=request),request)

def _inspection_template_name_for_lease(lease):
    """Choose the configured inspection template from the leased unit layout."""
    unit = lease.unit
    bedrooms = getattr(unit, "bedrooms", None)

    # A one-bedroom/single-room unit uses the compact Room template.
    # Two-bedroom and larger units use Apartment Standard.
    if bedrooms is not None:
        try:
            return "Room" if int(bedrooms) <= 1 else "Apartment Standard"
        except (TypeError, ValueError):
            pass

    # Compatibility fallback for projects that describe the unit/building type
    # textually instead of using the bedrooms field.
    type_text = " ".join(
        str(value or "")
        for value in (
            getattr(unit, "unit_type", ""),
            getattr(unit, "type", ""),
            getattr(getattr(unit, "property", None), "type", ""),
            getattr(getattr(unit, "property", None), "property_type", ""),
        )
    ).lower()
    return "Room" if "single room" in type_text or type_text.strip() == "room" else "Apartment Standard"


def _create_default_move_in_inspection(request, lease):
    from leases.models_inspections import InspectionTemplate, InspectionType, LeaseInspection

    inspection_type = InspectionType.objects.filter(
        name__iexact="Move In", active=True
    ).first()
    if inspection_type is None:
        raise RuntimeError("Active inspection type 'Move In' was not found.")

    template_name = _inspection_template_name_for_lease(lease)
    inspection_template = InspectionTemplate.objects.filter(
        name__iexact=template_name, active=True
    ).first()
    if inspection_template is None:
        raise RuntimeError(
            f"Active inspection template '{template_name}' was not found."
        )

    inspection = LeaseInspection.objects.create(
        lease=lease,
        property=lease.unit.property,
        unit=lease.unit,
        tenant=lease.tenant,
        inspection_type=inspection_type,
        inspection_template=inspection_template,
        inspection_date=timezone.localdate(),
        inspector=None,
        inspector_name="",
        status=LeaseInspection.STATUS_DRAFT,
        created_by=request.user if getattr(request.user, "is_authenticated", False) else None,
    )
    inspection.snapshot_template_items()
    inspection.add_audit(
        "created automatically for agreement package",
        request.user if getattr(request.user, "is_authenticated", False) else None,
        {"template": inspection_template.name, "type": inspection_type.name},
    )
    return inspection


def inspection_pdf(request, lease):
    # status is a CharField, not a relation, so it must never be passed to
    # select_related(). Select only actual foreign-key relations.
    inspection = (
        lease.inspections.select_related(
            "inspection_type",
            "inspection_template",
            "property",
            "unit",
            "tenant",
            "inspector",
            "approved_by",
        )
        .prefetch_related(
            "details__photos",
            "meter_readings",
            "keys",
            "appliances",
            "damage_charges__invoice",
        )
        .order_by("-inspection_date", "-id")
        .first()
    )

    if inspection is None:
        inspection = _create_default_move_in_inspection(request, lease)

    from leases.views_inspections import _inspection_pdf_bytes
    return _inspection_pdf_bytes(request, inspection)

def police_pdf(request, lease):
    return _pdf(render_to_string("leases/police_verification_summary_pdf.html",{"lease":lease,"tenant":lease.tenant,"property":lease.unit.property,"unit":lease.unit,"family_members":lease.family_members.select_related("family_member","relationship_type"),"vehicles":lease.vehicles.filter(is_active=True).select_related("vehicle_type"),"generated_at":timezone.localtime()},request=request),request)

def signature_pdf(request,lease,history=None,snapshot=None):
    snap=snapshot or party_snapshot(lease,history)
    return _pdf(render_to_string("leases/agreement_signature_page.html",{"lease":lease,"history":history,"parties":snap,"generated_at":timezone.now()},request=request),request)

def merge_pdfs(parts):
    try:
        from pypdf import PdfReader,PdfWriter
    except ImportError:
        from PyPDF2 import PdfReader,PdfWriter
    writer=PdfWriter()
    for part in parts:
        reader=PdfReader(BytesIO(part))
        for page in reader.pages: writer.add_page(page)
    out=BytesIO(); writer.write(out); return out.getvalue()

def build_package(request,lease,history,clauses):
    components=[]
    try: components.append(agreement_pdf(request,lease,history,clauses))
    except Exception as exc: raise RuntimeError(f"Agreement PDF generation failed: {exc}") from exc
    try: components.append(inspection_pdf(request,lease))
    except Exception as exc: raise RuntimeError(f"Agreement generated, but inspection PDF generation failed: {exc}") from exc
    try: components.append(police_pdf(request,lease))
    except Exception as exc: raise RuntimeError(f"Agreement and inspection generated, but police report generation failed: {exc}") from exc
    try: components.append(signature_pdf(request,lease,history))
    except Exception as exc: raise RuntimeError(f"Signature page generation failed: {exc}") from exc
    merged=merge_pdfs(components)
    name=f"lease-{lease.pk}"+(f"-renewal-{history.renewal_number}" if history and not history.is_original else "")+"-agreement-package.pdf"
    doc=LeaseDocument(lease=lease,lease_history=history,category="lease_renewal_agreement" if history and not history.is_original else "lease_agreement",display_name=name,original_filename=name,uploaded_by=request.user if request.user.is_authenticated else None)
    doc.file.save(name,ContentFile(merged),save=True)
    return merged,name,doc
