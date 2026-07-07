# core/views.py
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib import messages
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponseForbidden
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import timedelta
from decimal import Decimal

from django.db import models
from django.db.models import Case, DecimalField, F, OuterRef, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest

from .forms import GlobalSettingsForm
from .models import GlobalSettings
from tenants.models import Tenant, TenantInterestType
from payments.models import Payment
from invoices.models import Invoice
from invoices.models import SecurityDepositTransaction
from expenses.models import Expense
from properties.models import Property, Unit
from leases.models import Lease, LeaseRenewal
from smart_meter.models import LiveReading
from django.contrib.auth.decorators import login_required

METER_ONLINE_MINUTES = 3


PENDING_KIND_LABELS = {
    "lease": "Pending Lease",
    "agreement": "Pending Agreement Edit",
    "payment": "WhatsApp Payment",
    "media": "WhatsApp Document / Media",
    "maintenance": "WhatsApp Maintenance",
    "family": "Lease Family Member",
    "police": "Police Verification",
}


def _pending_item_urls(kind, item):
    urls = {
        "detail": reverse("core:pending_approval_detail", args=[kind, item.pk]),
        "approve": reverse("core:pending_approval_approve", args=[kind, item.pk]),
        "reject": reverse("core:pending_approval_reject", args=[kind, item.pk]),
    }
    if kind == "family":
        urls.update({
            "photo": reverse("core:pending_family_file", args=[item.pk, "photo"]),
            "cnic_front": reverse("core:pending_family_file", args=[item.pk, "cnic_front"]),
            "cnic_back": reverse("core:pending_family_file", args=[item.pk, "cnic_back"]),
        })
    return urls


def _property_unit_label(item):
    property_obj = getattr(item, "property", None)
    unit = getattr(item, "unit", None)
    lease = getattr(item, "lease", None)
    if lease:
        unit = getattr(lease, "unit", None)
        property_obj = getattr(unit, "property", property_obj)
    elif unit and not property_obj:
        property_obj = getattr(unit, "property", None)
    property_name = getattr(property_obj, "property_name", "") or str(property_obj or "")
    unit_number = getattr(unit, "unit_number", "") or str(unit or "")
    if property_name and unit_number:
        return f"{property_name} / {unit_number}"
    return property_name or unit_number or "-"


def _media_preview_kind(file_name, media_type=""):
    name = (file_name or "").lower()
    media_type = (media_type or "").lower()
    if media_type.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "image"
    if media_type.startswith("video/") or name.endswith((".mp4", ".mov", ".webm")):
        return "video"
    if media_type == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    return "file"


def _pending_media_context(media):
    if not media or not getattr(media, "file", None):
        return {"file_url": "", "preview_kind": "file"}
    return {
        "file_url": media.file.url,
        "preview_kind": _media_preview_kind(media.file.name, media.media_type),
        "filename": media.original_filename or media.file.name,
    }


@login_required
def pending_approvals(request):
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia, PendingWhatsAppPayment
    from leases.models import PendingAgreementApproval, PendingLeaseFamilyMemberSubmission, PendingPoliceVerificationSubmission

    pending_payments = PendingWhatsAppPayment.objects.filter(
        status__in=[PendingWhatsAppPayment.STATUS_PENDING, PendingWhatsAppPayment.STATUS_CONFIRMED],
        approved=False,
        rejected=False,
    ).select_related("tenant", "lease", "property", "unit")[:50]
    pending_media = PendingWhatsAppMedia.objects.filter(
        status=PendingWhatsAppMedia.STATUS_PENDING,
    ).select_related("tenant", "lease", "property", "unit")[:50]
    pending_maintenance = PendingWhatsAppMaintenance.objects.filter(
        status=PendingWhatsAppMaintenance.STATUS_PENDING,
    ).select_related("tenant", "lease", "property", "unit")[:50]
    pending_leases = Lease.objects.filter(status="pending_approval").select_related("tenant", "unit__property")[:50]
    pending_agreements = PendingAgreementApproval.objects.filter(
        status=PendingAgreementApproval.STATUS_PENDING,
    ).select_related("lease__tenant", "lease__unit__property", "submitted_by")[:50]
    pending_family = PendingLeaseFamilyMemberSubmission.objects.filter(
        status=PendingLeaseFamilyMemberSubmission.STATUS_PENDING,
    ).select_related("lease__tenant", "lease__unit__property", "primary_tenant", "relationship_type")[:50]
    pending_police = PendingPoliceVerificationSubmission.objects.filter(
        status=PendingPoliceVerificationSubmission.STATUS_PENDING,
    ).select_related("lease__tenant", "lease__unit__property", "tenant")[:50]
    sections = [
        {
            "title": "Pending Leases",
            "kind": "lease",
            "items": pending_leases,
            "count": Lease.objects.filter(status="pending_approval").count(),
        },
        {
            "title": "Pending Agreement Edits",
            "kind": "agreement",
            "items": pending_agreements,
            "count": PendingAgreementApproval.objects.filter(status=PendingAgreementApproval.STATUS_PENDING).count(),
        },
        {
            "title": "WhatsApp Payments",
            "kind": "payment",
            "items": pending_payments,
            "count": PendingWhatsAppPayment.objects.filter(
                status__in=[PendingWhatsAppPayment.STATUS_PENDING, PendingWhatsAppPayment.STATUS_CONFIRMED],
                approved=False,
                rejected=False,
            ).count(),
        },
        {
            "title": "WhatsApp Documents / Media",
            "kind": "media",
            "items": pending_media,
            "count": PendingWhatsAppMedia.objects.filter(status=PendingWhatsAppMedia.STATUS_PENDING).count(),
        },
        {
            "title": "WhatsApp Maintenance",
            "kind": "maintenance",
            "items": pending_maintenance,
            "count": PendingWhatsAppMaintenance.objects.filter(status=PendingWhatsAppMaintenance.STATUS_PENDING).count(),
        },
        {
            "title": "Lease Family Members",
            "kind": "family",
            "items": pending_family,
            "count": PendingLeaseFamilyMemberSubmission.objects.filter(status=PendingLeaseFamilyMemberSubmission.STATUS_PENDING).count(),
        },
        {
            "title": "Police Verification",
            "kind": "police",
            "items": pending_police,
            "count": PendingPoliceVerificationSubmission.objects.filter(status=PendingPoliceVerificationSubmission.STATUS_PENDING).count(),
        },
    ]
    for section in sections:
        section["items"] = [
            {
                "object": item,
                "urls": _pending_item_urls(section["kind"], item),
                "property_unit_label": _property_unit_label(item),
            }
            for item in section["items"]
        ]
    return render(request, "core/pending_approvals.html", {"sections": sections})


def _pending_item_for_kind(kind, pk):
    from leases.models import PendingAgreementApproval, PendingLeaseFamilyMemberSubmission, PendingPoliceVerificationSubmission
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia, PendingWhatsAppPayment

    if kind == "lease":
        return get_object_or_404(Lease.objects.select_related("tenant", "unit__property"), pk=pk)
    if kind == "agreement":
        return get_object_or_404(
            PendingAgreementApproval.objects.select_related("lease__tenant", "lease__unit__property", "submitted_by"),
            pk=pk,
        )
    if kind == "payment":
        return get_object_or_404(
            PendingWhatsAppPayment.objects.select_related("tenant", "lease", "property", "unit", "created_payment"),
            pk=pk,
        )
    if kind == "media":
        return get_object_or_404(
            PendingWhatsAppMedia.objects.select_related("tenant", "lease", "property", "unit", "approved_by"),
            pk=pk,
        )
    if kind == "maintenance":
        return get_object_or_404(
            PendingWhatsAppMaintenance.objects.select_related("tenant", "lease", "property", "unit", "created_request", "approved_by")
            .prefetch_related("media"),
            pk=pk,
        )
    if kind == "family":
        return get_object_or_404(
            PendingLeaseFamilyMemberSubmission.objects.select_related(
                "lease__tenant",
                "lease__unit__property",
                "primary_tenant",
                "relationship_type",
                "created_tenant",
                "created_family_member",
                "reviewed_by",
            ),
            pk=pk,
        )
    if kind == "police":
        return get_object_or_404(
            PendingPoliceVerificationSubmission.objects.select_related(
                "lease__tenant",
                "lease__unit__property",
                "tenant",
                "reviewed_by",
                "approved_document",
            ),
            pk=pk,
        )
    raise Http404("Unknown pending approval type.")


@login_required
def pending_approval_detail(request, kind, pk):
    item = _pending_item_for_kind(kind, pk)
    media_items = []
    media_preview = None
    if kind == "media":
        media_preview = _pending_media_context(item)
    elif kind == "payment":
        if getattr(item, "screenshot", None):
            media_preview = {
                "file_url": item.screenshot.url,
                "preview_kind": _media_preview_kind(item.screenshot.name),
                "filename": item.screenshot.name,
            }
    elif kind == "maintenance":
        media_items = [
            {"object": media, **_pending_media_context(media)}
            for media in item.media.all()
        ]
    elif kind == "police":
        media_preview = {
            "file_url": item.file.url if item.file else "",
            "preview_kind": _media_preview_kind(item.file.name if item.file else ""),
            "filename": item.original_filename or (item.file.name if item.file else ""),
        }
    return render(
        request,
        "core/pending_approval_detail.html",
        {
            "kind": kind,
            "kind_label": PENDING_KIND_LABELS.get(kind, "Pending Approval"),
            "item": item,
            "media_preview": media_preview,
            "media_items": media_items,
            "property_unit_label": _property_unit_label(item),
            "urls": _pending_item_urls(kind, item),
        },
    )


@login_required
def pending_family_file(request, pk, field_name):
    from leases.models import PendingLeaseFamilyMemberSubmission

    if field_name not in {"photo", "cnic_front", "cnic_back"}:
        raise Http404("Unknown file field.")
    item = get_object_or_404(PendingLeaseFamilyMemberSubmission, pk=pk)
    file_field = getattr(item, field_name)
    if not file_field:
        raise Http404("File not found.")
    try:
        return FileResponse(file_field.open("rb"), filename=file_field.name.rsplit("/", 1)[-1])
    except FileNotFoundError:
        raise Http404("File is missing from storage.")


def _attach_pending_media_from_core(pending, user):
    from leases.models import LeaseDocument
    from properties.models import PropertyMedia, UnitMedia
    from whatsapp.models import PendingWhatsAppMedia

    if not pending.file:
        raise ValueError("No file is attached.")
    pending.file.open("rb")
    content = ContentFile(pending.file.read(), name=pending.original_filename or pending.file.name)
    pending.file.close()
    if pending.purpose == PendingWhatsAppMedia.PURPOSE_PROPERTY and pending.property_id:
        PropertyMedia.objects.create(
            property=pending.property,
            file=content,
            description=pending.ai_notes[:300],
            uploaded_by=user,
            original_filename=pending.original_filename,
        )
        return
    if pending.purpose == PendingWhatsAppMedia.PURPOSE_UNIT and pending.unit_id:
        UnitMedia.objects.create(
            unit=pending.unit,
            file=content,
            description=pending.ai_notes[:300],
            uploaded_by=user,
            original_filename=pending.original_filename,
        )
        return
    if pending.purpose == PendingWhatsAppMedia.PURPOSE_LEASE and pending.lease_id:
        LeaseDocument.objects.create(
            lease=pending.lease,
            file=content,
            original_filename=pending.original_filename,
            display_name=pending.original_filename or "WhatsApp lease document",
            category="other",
            description=pending.ai_notes,
            uploaded_by=user,
        )
        return
    if pending.purpose in {
        PendingWhatsAppMedia.PURPOSE_OTHER,
        PendingWhatsAppMedia.PURPOSE_PAYMENT,
        PendingWhatsAppMedia.PURPOSE_MAINTENANCE,
    }:
        return
    raise ValueError("This media needs a Property, Unit, or Lease Document target before approval.")


def _approve_pending_payment(pending, user):
    from whatsapp.models import PendingWhatsAppPayment

    if pending.approved or pending.rejected:
        raise ValueError("This payment has already been reviewed.")
    if not pending.lease_id or not pending.amount:
        raise ValueError("Payment needs a lease and amount before approval.")
    payment = Payment.objects.create(
        lease=pending.lease,
        payment_date=pending.date or timezone.localdate(),
        amount=pending.amount,
        reference_number=pending.reference or "",
        notes=f"Created from WhatsApp pending payment #{pending.pk}. {pending.ai_notes}",
    )
    pending.created_payment = payment
    pending.approved = True
    pending.rejected = False
    pending.status = PendingWhatsAppPayment.STATUS_APPROVED
    pending.approved_by = user
    pending.approved_at = timezone.now()
    pending.save(update_fields=[
        "created_payment", "approved", "rejected", "status", "approved_by", "approved_at", "updated_at"
    ])


def _approve_pending_maintenance(pending, user):
    from maintenance.models import MaintenanceRequest, MaintenanceRequestMedia
    from whatsapp.models import PendingWhatsAppMaintenance

    if pending.status != PendingWhatsAppMaintenance.STATUS_PENDING:
        raise ValueError("This maintenance submission has already been reviewed.")
    if not pending.unit_id:
        raise ValueError("Maintenance needs a unit before approval.")
    ticket = MaintenanceRequest.objects.create(
        lease=pending.lease,
        unit=pending.unit,
        tenant=pending.tenant,
        title=pending.issue_type or "WhatsApp Maintenance",
        description=pending.description,
        source=MaintenanceRequest.SOURCE_MANUAL,
        category=pending.issue_type or "General",
        priority="urgent" if pending.urgency in {"urgent", "emergency"} else "normal",
        created_by=user,
    )
    for media in pending.media.all():
        if not media.file:
            continue
        media.file.open("rb")
        MaintenanceRequestMedia.objects.create(
            request=ticket,
            file=ContentFile(media.file.read(), name=media.original_filename or media.file.name),
            description=media.ai_notes[:255],
            uploaded_by=user,
            original_filename=media.original_filename,
        )
        media.file.close()
    pending.created_request = ticket
    pending.status = PendingWhatsAppMaintenance.STATUS_APPROVED
    pending.approved_by = user
    pending.approved_at = timezone.now()
    pending.save(update_fields=["created_request", "status", "approved_by", "approved_at", "updated_at"])


@login_required
@require_POST
def pending_approval_approve(request, kind, pk):
    from leases.models import PendingAgreementApproval
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia

    item = _pending_item_for_kind(kind, pk)
    try:
        if kind == "lease":
            if item.status != "pending_approval":
                raise ValueError("This lease is not pending approval.")
            item.status = "active"
            item.save(update_fields=["status", "updated_at"])
            messages.success(request, "Lease approved and activated.")
            return redirect("leases:lease_detail", pk=item.pk)
        if kind == "agreement":
            if item.status != PendingAgreementApproval.STATUS_PENDING:
                raise ValueError("This agreement edit is not pending.")
            item.status = PendingAgreementApproval.STATUS_APPROVED
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.lease.terms = item.proposed_terms
            item.lease.save(update_fields=["terms", "updated_at"])
            item.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
            messages.success(request, "Agreement edit approved and applied.")
            return redirect("leases:lease_detail", pk=item.lease_id)
        if kind == "family":
            from leases.views import approve_pending_family_submission
            approve_pending_family_submission(item, request.user)
            if getattr(item, "action", "") == "remove":
                messages.success(request, "Family member removal approved.")
            else:
                messages.success(request, "Family member approved and added to lease.")
            return redirect("leases:lease_detail", pk=item.lease_id)
        if kind == "police":
            from leases.services.police_verification import approve_police_submission
            approve_police_submission(item, request.user)
            messages.success(request, "Police verification approved and attached to the lease.")
            return redirect("leases:lease_detail", pk=item.lease_id)
        if kind == "payment":
            _approve_pending_payment(item, request.user)
            messages.success(request, "WhatsApp payment approved and posted.")
        elif kind == "media":
            if item.status != PendingWhatsAppMedia.STATUS_PENDING:
                raise ValueError("This media has already been reviewed.")
            _attach_pending_media_from_core(item, request.user)
            item.status = PendingWhatsAppMedia.STATUS_APPROVED
            item.approved_by = request.user
            item.approved_at = timezone.now()
            item.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
            messages.success(request, "WhatsApp media approved.")
        elif kind == "maintenance":
            _approve_pending_maintenance(item, request.user)
            messages.success(request, "Maintenance request approved and created.")
            if item.created_request_id:
                return redirect("maintenance:request_detail", pk=item.created_request_id)
        else:
            raise Http404("Unknown pending approval type.")
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("core:pending_approval_detail", kind=kind, pk=pk)
    return redirect("core:pending_approvals")


@login_required
@require_POST
def pending_approval_reject(request, kind, pk):
    from leases.models import PendingAgreementApproval, PendingLeaseFamilyMemberSubmission, PendingPoliceVerificationSubmission
    from whatsapp.models import PendingWhatsAppMaintenance, PendingWhatsAppMedia, PendingWhatsAppPayment

    item = _pending_item_for_kind(kind, pk)
    if kind == "agreement":
        item.status = PendingAgreementApproval.STATUS_REJECTED
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.review_notes = request.POST.get("review_notes", "")
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
    elif kind == "payment":
        item.rejected = True
        item.approved = False
        item.status = PendingWhatsAppPayment.STATUS_REJECTED
        item.save(update_fields=["rejected", "approved", "status", "updated_at"])
    elif kind == "media":
        item.status = PendingWhatsAppMedia.STATUS_REJECTED
        item.save(update_fields=["status", "updated_at"])
    elif kind == "maintenance":
        item.status = PendingWhatsAppMaintenance.STATUS_REJECTED
        item.save(update_fields=["status", "updated_at"])
    elif kind == "lease":
        item.status = "rejected"
        item.save(update_fields=["status", "updated_at"])
    elif kind == "family":
        item.status = PendingLeaseFamilyMemberSubmission.STATUS_REJECTED
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.review_notes = request.POST.get("review_notes", "")
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])
    elif kind == "police":
        item.status = PendingPoliceVerificationSubmission.STATUS_REJECTED
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.notes = "\n".join(part for part in [item.notes, request.POST.get("review_notes", "")] if part).strip()
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "notes"])
    else:
        raise Http404("Unknown pending approval type.")
    messages.success(request, "Pending item rejected.")
    return redirect("core:pending_approvals")


def _annotate_dashboard_lease_financials(queryset):
    money_field = DecimalField(max_digits=12, decimal_places=2)
    zero = Value(Decimal("0.00"), output_field=money_field)
    today = timezone.localdate()

    active_history_monthly_payment = (
        LeaseRenewal.objects.filter(
            lease_id=OuterRef("pk"),
            start_date__lte=today,
            end_date__gte=today,
        )
        .annotate(
            total=(
                Coalesce(F("monthly_rent"), zero)
                + Coalesce(F("society_maintenance"), zero)
                + Coalesce(F("water_charges"), zero)
                + Coalesce(F("internet_charges"), zero)
            )
        )
        .order_by("-renewal_number", "-id")
        .values("total")[:1]
    )

    invoice_total = (
        Invoice.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(total=Coalesce(Sum("amount"), zero))
        .values("total")[:1]
    )

    payment_total = (
        Payment.objects.filter(lease_id=OuterRef("pk"))
        .values("lease_id")
        .annotate(
            total=Coalesce(
                Sum(
                    Case(
                        When(
                            detail__isnull=False,
                            then=F("detail__lease_amount"),
                        ),
                        default=F("amount"),
                        output_field=money_field,
                    )
                ),
                zero,
            )
        )
        .values("total")[:1]
    )

    def security_total(tx_type):
        return (
            SecurityDepositTransaction.objects.filter(
                lease_id=OuterRef("pk"),
                type=tx_type,
            )
            .values("lease_id")
            .annotate(total=Coalesce(Sum("amount"), zero))
            .values("total")[:1]
        )

    return (
        queryset.annotate(
            invoice_total=Coalesce(Subquery(invoice_total, output_field=money_field), zero),
            payment_total=Coalesce(Subquery(payment_total, output_field=money_field), zero),
            security_paid_total=Coalesce(
                Subquery(security_total("PAYMENT"), output_field=money_field),
                zero,
            ),
            security_adjust_total=Coalesce(
                Subquery(security_total("ADJUST"), output_field=money_field),
                zero,
            ),
        )
        .annotate(
            list_balance=F("invoice_total") - F("payment_total"),
            list_security_due=Greatest(
                Coalesce(F("security_deposit"), zero)
                - F("security_paid_total")
                - F("security_adjust_total"),
                zero,
                output_field=money_field,
            ),
            list_monthly_payment=Coalesce(
                Subquery(active_history_monthly_payment, output_field=money_field),
                Coalesce(F("monthly_rent"), zero)
                + Coalesce(F("society_maintenance"), zero)
                + Coalesce(F("water_charges"), zero)
                + Coalesce(F("internet_charges"), zero),
                output_field=money_field,
            ),
        )
    )


@login_required
def dashboard(request):
    today = timezone.now().date()
    thirty_days_ago = today - timedelta(days=30)
    lease_ending_cutoff = today + timedelta(days=40)
    recently_ended_cutoff = today - timedelta(days=40)

    total_properties = Property.objects.count()
    total_units = Unit.objects.count()
    active_lease = Lease.objects.filter(
        unit_id=models.OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    ).exclude(status__in=["ended", "terminated"])
    active_lease_history = LeaseRenewal.objects.filter(
        lease__unit_id=models.OuterRef("pk"),
        start_date__lte=today,
        end_date__gte=today,
    )
    units_with_occupancy = Unit.objects.annotate(
        has_active_lease=models.Exists(active_lease),
        has_active_lease_history=models.Exists(active_lease_history),
    )
    occupied_units = units_with_occupancy.filter(
        models.Q(has_active_lease=True) | models.Q(has_active_lease_history=True)
    ).count()
    vacant_units = (
        units_with_occupancy.select_related("property", "interest_type")
        .filter(has_active_lease=False, has_active_lease_history=False)
        .exclude(status="maintenance")
        .order_by("property__property_name", "unit_number")[:10]
    )
    vacancy_rate = ((total_units - occupied_units) /
                    total_units * 100) if total_units > 0 else 0

    total_tenants = Tenant.objects.filter(is_active=True).count()

    total_rent = Invoice.objects.filter(
        description__contains='Monthly Rent',
        issue_date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    total_payments = Payment.objects.filter(
        payment_date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    total_expenses = Expense.objects.filter(
        date__range=[thirty_days_ago, today]
    ).aggregate(total=models.Sum('amount'))['total'] or 0

    recent_payments = list(
        Payment.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .order_by("-payment_date", "-id")[:5]
    )
    recent_lease_ids = [
        payment.lease_id for payment in recent_payments if payment.lease_id
    ]

    invoice_totals = {
        row["lease_id"]: row["total"] or 0
        for row in (
            Invoice.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(total=models.Sum("amount"))
        )
    }
    payment_totals = {
        row["lease_id"]: row["total"] or 0
        for row in (
            Payment.objects.filter(lease_id__in=recent_lease_ids)
            .values("lease_id")
            .annotate(total=models.Sum("amount"))
        )
    }

    for payment in recent_payments:
        payment.dashboard_balance = (
            invoice_totals.get(payment.lease_id, 0)
            - payment_totals.get(payment.lease_id, 0)
        )

    upcoming_invoices = (
        Invoice.objects.select_related(
            "lease",
            "lease__tenant",
            "lease__unit",
            "lease__unit__property",
        )
        .filter(due_date__gte=today, status__in=["unpaid", "partially_paid"])
        .order_by("due_date", "id")[:5]
    )

    meter_offline_cutoff = timezone.now() - timedelta(minutes=METER_ONLINE_MINUTES)
    offline_meter_readings = (
        LiveReading.objects.select_related(
            "meter",
            "meter__unit",
            "meter__unit__property",
        )
        .filter(
            meter__is_active=True,
            ts__lt=meter_offline_cutoff,
        )
        .order_by("ts", "meter__unit__property__property_name", "meter__unit__unit_number")[:50]
    )

    dashboard_lease_base = Lease.objects.select_related(
        "tenant",
        "unit",
        "unit__property",
    ).only(
        "id",
        "tenant_id",
        "unit_id",
        "start_date",
        "end_date",
        "monthly_rent",
        "society_maintenance",
        "water_charges",
        "internet_charges",
        "security_deposit",
        "status",
        "tenant__id",
        "tenant__first_name",
        "tenant__last_name",
        "tenant__phone",
        "unit__id",
        "unit__property_id",
        "unit__unit_number",
        "unit__property__id",
        "unit__property__property_name",
    )
    dashboard_leases = _annotate_dashboard_lease_financials(dashboard_lease_base)

    ending_soon_leases = (
        dashboard_leases.filter(
            models.Q(status="active", end_date__lte=lease_ending_cutoff)
            | models.Q(status__in=["ended", "inactive"], end_date__gte=recently_ended_cutoff)
        )
        .order_by("end_date", "unit__property__property_name", "unit__unit_number")[:10]
    )

    lease_balances = (
        dashboard_leases.filter(list_balance__gt=0)
        .order_by("-list_balance", "unit__property__property_name", "unit__unit_number")[:10]
    )

    recent_expenses = (
        Expense.objects.select_related("property", "unit", "category")
        .prefetch_related("receipts", "distributions__unit")
        .order_by("-date", "-pk")[:10]
    )

    context = {
        'total_properties': total_properties,
        'TODAY': today,
        'total_units': total_units,
        'occupied_units': occupied_units,
        'vacancy_rate': round(vacancy_rate, 2),
        'vacant_units': vacant_units,
        'total_tenants': total_tenants,
        'total_rent': total_rent,
        'total_payments': total_payments,
        'total_expenses': total_expenses,
        'net_income': total_payments - total_expenses,
        'recent_payments': recent_payments,
        'recent_invoices': Invoice.objects.order_by('-issue_date')[:5],
        'upcoming_invoices': upcoming_invoices,
        'offline_meter_readings': offline_meter_readings,
        'meter_online_minutes': METER_ONLINE_MINUTES,
        'ending_soon_leases': ending_soon_leases,
        'recent_expenses': recent_expenses,
        'lease_balances': lease_balances,
    }
    return render(request, 'dashboard.html', context)


class SettingsView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    # <— moved from tms_config/...
    template_name = "core/settings.html"
    form_class = GlobalSettingsForm
    success_url = reverse_lazy("core:settings")          # <— update namespace

    def test_func(self):
        return self.request.user.is_staff or self.request.user.is_superuser

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        # If you use django-solo, keep get_solo(); otherwise fallback to first-or-create:
        try:
            instance = GlobalSettings.get_solo()
        except AttributeError:
            instance, _ = GlobalSettings.objects.get_or_create(pk=1)
        kw["instance"] = instance
        return kw

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)
# core/views.py
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.utils.text import slugify
from .models import PaymentMethod


@require_POST
def payment_method_quick_add(request):
    """
    Quick add a payment method.
    Expects 'name' in POST, returns JSON {id, name}.
    """
    name = (request.POST.get('name') or '').strip()
    if not name:
        return HttpResponseBadRequest("Missing name")

    code = slugify(name) or 'method'
    # ensure unique code
    base_code = code
    i = 1
    while PaymentMethod.objects.filter(code=code).exists():
        i += 1
        code = f"{base_code}-{i}"

    pm = PaymentMethod.objects.create(
        name=name,
        code=code,
        is_active=True,
        sort_order=50,  # default
    )
    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
    })


@require_POST
def payment_method_quick_edit(request):
    """
    Quick edit the name of an existing payment method.
    Expects 'id' and 'name' in POST.
    """
    try:
        pm_id = int(request.POST.get('id'))
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid id")

    name = (request.POST.get('name') or '').strip()
    if not name:
        return HttpResponseBadRequest("Missing name")

    try:
        pm = PaymentMethod.objects.get(pk=pm_id)
    except PaymentMethod.DoesNotExist:
        return HttpResponseBadRequest("Payment method not found")

    pm.name = name
    pm.save(update_fields=['name'])

    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
    })
# core/views.py

from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from .models import PaymentMethod


def payment_method_get(request, pk):
    pm = get_object_or_404(PaymentMethod, pk=pk)
    return JsonResponse({
        "id": pm.id,
        "name": pm.name,
        "code": pm.code,
        "sort_order": pm.sort_order,
        "is_active": pm.is_active,
    })


@require_POST
def payment_method_toggle(request, pk):
    pm = get_object_or_404(PaymentMethod, pk=pk)
    pm.is_active = not pm.is_active
    pm.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def payment_method_save(request):
    pm_id = request.POST.get("id")
    name = request.POST.get("name", "").strip()
    code = request.POST.get("code", "").strip() or slugify(name)
    sort_order = int(request.POST.get("sort_order", "50"))
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")

    if pm_id:
        pm = get_object_or_404(PaymentMethod, pk=pm_id)
    else:
        pm = PaymentMethod()

    pm.name = name
    pm.code = code
    pm.sort_order = sort_order
    pm.is_active = is_active
    pm.save()

    return JsonResponse({"ok": True})

# core/views.py
from django.shortcuts import render, redirect
from django.contrib import messages

from .models import GlobalSettings, PaymentMethod
from .forms import GlobalSettingsForm
from leases.models import LeaseDocumentCategory, LeaseRelationshipType
from django.conf import settings
# core/views.py
from django.views.generic import FormView
from django.urls import reverse_lazy
from django.contrib import messages
from django.core.cache import cache

from .models import GlobalSettings, PaymentMethod
from .forms import GlobalSettingsForm


class SettingsView(FormView):
    template_name = "core/settings.html"
    form_class = GlobalSettingsForm
    success_url = reverse_lazy("core:settings")

    def get_form_kwargs(self):
        """
        Use the singleton GlobalSettings instance.
        """
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = GlobalSettings.get_solo()
        return kwargs

    def form_valid(self, form):
        form.save()
        cache.delete("core.global_settings")
        cache.delete("core.enable_debug_toolbar")
        messages.success(self.request, "Settings saved.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        """
        Add payment_methods so settings.html can render the list.
        """
        ctx = super().get_context_data(**kwargs)
        form = ctx.get("form")
        if form:
            ctx["settings_field_groups"] = [
                (title, icon, [form[name] for name in names if name in form.fields])
                for title, icon, names in form.FIELD_GROUPS
            ]
        ctx["payment_methods"] = PaymentMethod.objects.order_by(
            "sort_order", "name"
        )
        doc_cat_sort = self.request.GET.get("doc_cat_sort")
        ctx["doc_cat_sort"] = doc_cat_sort
        doc_cat_ordering = ("name", "sort_order") if doc_cat_sort == "name" else ("sort_order", "name")
        ctx["lease_document_categories"] = LeaseDocumentCategory.objects.order_by(*doc_cat_ordering)
        ctx["tenant_interest_types"] = TenantInterestType.objects.order_by(
            "sort_order", "name"
        )
        ctx["lease_relationship_types"] = LeaseRelationshipType.objects.order_by("name")
        try:
            from whatsapp.services.ai_config import get_whatsapp_ai_config
            from whatsapp.services.whatsapp import WhatsAppService

            ai_config = get_whatsapp_ai_config()
            whatsapp_config = WhatsAppService().configuration_status()
        except Exception:
            ai_config = None
            whatsapp_config = {}
        ctx["whatsapp_ai_status"] = {
            "openai_key_configured": bool(getattr(settings, "OPENAI_API_KEY", "")),
            "whatsapp_token_configured": bool(getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")),
            "whatsapp_api_ok": bool(whatsapp_config.get("ok")),
            "whatsapp_missing": whatsapp_config.get("missing", []),
            "celery_broker": getattr(settings, "CELERY_BROKER_URL", ""),
            "runtime": "Celery" if ai_config and ai_config.use_celery else "Local thread fallback",
            "provider": getattr(ai_config, "provider", ""),
            "ocr_provider": getattr(ai_config, "ocr_provider", ""),
        }
        return ctx


@login_required
@require_POST
def test_whatsapp_pending_request_alert(request):
    config = GlobalSettings.get_solo()
    raw_numbers = (
        request.POST.get("whatsapp_pending_request_staff_numbers")
        or config.whatsapp_pending_request_staff_numbers
        or ""
    )
    numbers = _split_whatsapp_staff_numbers(raw_numbers)
    if not numbers:
        messages.error(request, "Add at least one pending request staff WhatsApp number before testing.")
        return redirect(f"{reverse('core:settings')}#settings-group-whatsapp-twilio")

    from whatsapp.services.whatsapp import WhatsAppService

    service = WhatsAppService(created_by=request.user)
    sent = 0
    failed = []
    body = (
        "Test WhatsApp pending request alert from TMS.\n\n"
        "If you received this, pending request notifications are configured."
    )
    for number in numbers:
        result = service.send_text(number, body)
        if result.get("ok"):
            sent += 1
        else:
            failed.append(f"{number}: {result.get('error') or 'failed'}")

    if sent:
        messages.success(request, f"Test pending request alert sent to {sent} number(s).")
    if failed:
        messages.error(request, "Some test alerts failed: " + " | ".join(failed[:3]))
    return redirect(f"{reverse('core:settings')}#settings-group-whatsapp-twilio")


def _split_whatsapp_staff_numbers(raw_numbers):
    cleaned = str(raw_numbers or "").replace(";", ",").replace("\n", ",")
    numbers = []
    seen = set()
    for item in cleaned.split(","):
        number = item.strip()
        if number and number not in seen:
            seen.add(number)
            numbers.append(number)
    return numbers


def lease_document_category_get(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    return JsonResponse({
        "id": category.id,
        "name": category.name,
        "code": category.code,
        "sort_order": category.sort_order,
        "is_active": category.is_active,
    })


@require_POST
def lease_document_category_toggle(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    category.is_active = not category.is_active
    category.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def lease_document_category_save(request):
    category_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if category_id:
        category = get_object_or_404(LeaseDocumentCategory, pk=category_id)
    else:
        category = LeaseDocumentCategory()

    category.name = name
    category.code = code
    category.sort_order = sort_order
    category.is_active = is_active
    category.save()
    return JsonResponse({"ok": True})


@require_POST
def lease_document_category_inline_update(request, pk):
    category = get_object_or_404(LeaseDocumentCategory, pk=pk)
    field = request.POST.get("field")
    value = (request.POST.get("value") or "").strip()
    if field == "name":
        if not value:
            return HttpResponseBadRequest("Name required")
        category.name = value
    elif field == "code":
        if not value:
            return HttpResponseBadRequest("Code required")
        category.code = value
    elif field == "sort_order":
        try:
            category.sort_order = int(value or 0)
        except ValueError:
            return HttpResponseBadRequest("Invalid sort order")
    elif field == "is_active":
        category.is_active = value in ("1", "true", "on", "yes")
    else:
        return HttpResponseBadRequest("Invalid field")
    category.save(update_fields=[field])
    return JsonResponse({"ok": True})


def tenant_interest_type_get(request, pk):
    interest_type = get_object_or_404(TenantInterestType, pk=pk)
    return JsonResponse({
        "id": interest_type.id,
        "name": interest_type.name,
        "code": interest_type.code,
        "sort_order": interest_type.sort_order,
        "is_active": interest_type.is_active,
    })


@require_POST
def tenant_interest_type_toggle(request, pk):
    interest_type = get_object_or_404(TenantInterestType, pk=pk)
    interest_type.is_active = not interest_type.is_active
    interest_type.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def tenant_interest_type_save(request):
    interest_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if interest_id:
        interest_type = get_object_or_404(TenantInterestType, pk=interest_id)
    else:
        interest_type = TenantInterestType()

    interest_type.name = name
    interest_type.code = code
    interest_type.sort_order = sort_order
    interest_type.is_active = is_active
    interest_type.save()
    return JsonResponse({"ok": True})


def lease_relationship_type_get(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    return JsonResponse({
        "id": relationship_type.id,
        "name": relationship_type.name,
        "code": relationship_type.code,
        "sort_order": relationship_type.sort_order,
        "is_active": relationship_type.is_active,
    })


@require_POST
def lease_relationship_type_toggle(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    relationship_type.is_active = not relationship_type.is_active
    relationship_type.save(update_fields=["is_active"])
    return JsonResponse({"ok": True})


@require_POST
def lease_relationship_type_save(request):
    relationship_id = request.POST.get("id")
    name = (request.POST.get("name") or "").strip()
    code = (request.POST.get("code") or "").strip() or slugify(name)
    try:
        sort_order = int(request.POST.get("sort_order", "50"))
    except ValueError:
        sort_order = 50
    is_active = request.POST.get("is_active") == "1"

    if not name:
        return HttpResponseBadRequest("Name required")
    if not code:
        return HttpResponseBadRequest("Code required")

    if relationship_id:
        relationship_type = get_object_or_404(LeaseRelationshipType, pk=relationship_id)
    else:
        relationship_type = LeaseRelationshipType()

    relationship_type.name = name
    relationship_type.code = code
    relationship_type.sort_order = sort_order
    relationship_type.is_active = is_active
    relationship_type.save()
    return JsonResponse({"ok": True})


@require_POST
def lease_relationship_type_inline_update(request, pk):
    relationship_type = get_object_or_404(LeaseRelationshipType, pk=pk)
    field = request.POST.get("field")
    value = (request.POST.get("value") or "").strip()
    if field == "name":
        if not value:
            return HttpResponseBadRequest("Name required")
        relationship_type.name = value
    elif field == "code":
        if not value:
            return HttpResponseBadRequest("Code required")
        relationship_type.code = value
    elif field == "is_active":
        relationship_type.is_active = value in ("1", "true", "on", "yes")
    else:
        return HttpResponseBadRequest("Invalid field")
    relationship_type.save(update_fields=[field])
    return JsonResponse({"ok": True})


from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from .forms import BackupRestoreForm, BackupSettingsForm, BackupUploadForm
from .backup_utils import (
    choices_for,
    create_code_backup,
    create_db_backup,
    create_full_backup,
    create_media_backup,
    delete_backup,
    list_backups,
    load_backup_settings,
    prune_old_backups,
    restore_database,
    restore_full,
    restore_media,
    save_backup_settings,
    save_uploaded_backup,
)


class BackupCenterView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "core/backup_center.html"

    def test_func(self):
        return self.request.user.is_superuser

    def _context(self, settings_form=None):
        config = load_backup_settings()
        backups = list_backups(config)
        selected_db = self.request.GET.get("selected_db")
        selected_media = self.request.GET.get("selected_media")
        selected_full = self.request.GET.get("selected_full")
        return {
            "backup_settings_form": settings_form or BackupSettingsForm(initial=config),
            "db_restore_form": BackupRestoreForm(
                backup_choices=choices_for(backups, "db"),
                initial={"backup_id": selected_db},
            ),
            "media_restore_form": BackupRestoreForm(
                backup_choices=choices_for(backups, "media"),
                initial={"backup_id": selected_media},
            ),
            "full_restore_form": BackupRestoreForm(
                backup_choices=choices_for(backups, "full"),
                initial={"backup_id": selected_full},
            ),
            "backup_upload_form": BackupUploadForm(),
            "backups": backups,
            "fresh_reset_scope": {
                "profile_name": "tms_safe",
                "profile_description": "Fresh reset is disabled until a TMS-specific reset profile is configured.",
                "wipe_total_rows": 0,
                "keep_total_rows": 0,
            },
        }

    def get(self, request):
        return render(request, self.template_name, self._context())

    def post(self, request):
        action = request.POST.get("action")
        config = load_backup_settings()
        try:
            if action == "save_backup_settings":
                form = BackupSettingsForm(request.POST)
                if form.is_valid():
                    save_backup_settings(form.cleaned_data)
                    messages.success(request, "Backup settings saved.")
                    return redirect("core:backup_center")
                return render(request, self.template_name, self._context(settings_form=form))

            if action == "upload_backup":
                form = BackupUploadForm(request.POST, request.FILES)
                if form.is_valid():
                    uploaded = save_uploaded_backup(
                        config,
                        form.cleaned_data["backup_type"],
                        form.cleaned_data["backup_file"],
                    )
                    messages.success(request, f"Backup uploaded: {uploaded.name}")
                else:
                    messages.error(request, "Upload failed. Check the selected type and file extension.")
                return redirect("core:backup_center")

            if action == "backup_db":
                created = create_db_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Database backup created: {created.name}")
            elif action == "backup_media":
                created = create_media_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Media backup created: {created.name}")
            elif action == "backup_code":
                created = create_code_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Code backup created: {created.name}")
            elif action == "backup_full":
                created = create_full_backup(config)
                prune_old_backups(config)
                messages.success(request, f"Full backup created: {created.name}")
            elif action == "restore_db":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "db"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE DB":
                    messages.error(request, "Type RESTORE DB exactly before restoring the database.")
                else:
                    safety = create_db_backup({**config, "enable_db_backup": True})
                    restore_database(config, form.cleaned_data["backup_id"])
                    messages.success(request, f"Database restore completed. Safety backup created first: {safety.name}")
            elif action == "restore_media":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "media"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE MEDIA":
                    messages.error(request, "Type RESTORE MEDIA exactly before restoring media.")
                else:
                    safety = create_media_backup({**config, "enable_media_backup": True})
                    restore_media(config, form.cleaned_data["backup_id"])
                    messages.success(request, f"Media restore completed. Safety backup created first: {safety.name}")
            elif action == "restore_full":
                form = BackupRestoreForm(request.POST, backup_choices=choices_for(list_backups(config), "full"))
                if not form.is_valid() or form.cleaned_data["confirm_text"] != "RESTORE FULL":
                    messages.error(request, "Type RESTORE FULL exactly before restoring a full backup.")
                else:
                    safety = create_full_backup({**config, "enable_full_backup": True})
                    restore_full(config, form.cleaned_data["backup_id"])
                    messages.success(request, f"Full restore completed. Safety backup created first: {safety.name}")
            elif action == "fresh_reset":
                messages.error(request, "Fresh reset is intentionally disabled until a TMS reset profile is configured.")
            else:
                messages.error(request, "Unknown backup action.")
        except Exception as exc:
            messages.error(request, f"Backup action failed: {exc}")

        return redirect("core:backup_center")


from django.http import FileResponse


class BackupDownloadView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def get(self, request, backup_id):
        backup = next((item for item in list_backups(load_backup_settings()) if item.id == backup_id), None)
        if not backup:
            raise Http404("Backup not found")
        return FileResponse(open(backup.display_path, "rb"), as_attachment=True, filename=backup.id)


class BackupDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        return self.request.user.is_superuser

    def post(self, request, backup_id):
        try:
            delete_backup(load_backup_settings(), backup_id)
        except Exception as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)
        return JsonResponse({"success": True})


from django.http import Http404, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from .forms import SuggestionReplyForm, SuggestionTicketForm
from .suggestion_store import (
    STATUS_CHOICES,
    TYPE_CHOICES,
    add_reply,
    create_ticket,
    delete_ticket,
    get_ticket,
    list_tickets,
    update_status,
)


@login_required
def suggestion_list(request):
    selected_status = request.GET.get("status")
    if selected_status is None:
        selected_status = "PENDING"
    selected_type = request.GET.get("type", "")
    tickets = list_tickets(status=selected_status, ticket_type=selected_type)
    return render(request, "core/suggestion_list.html", {
        "tickets": tickets,
        "status_choices": STATUS_CHOICES,
        "type_choices": TYPE_CHOICES,
        "selected_status": selected_status,
        "selected_type": selected_type,
    })


@login_required
def suggestion_create(request):
    if request.method == "POST":
        form = SuggestionTicketForm(request.POST)
        if form.is_valid():
            ticket = create_ticket(
                form.cleaned_data,
                request.user,
                files=request.FILES.getlist("photos"),
            )
            messages.success(request, "Suggestion saved.")
            return redirect("core:suggestion_detail", pk=ticket.id)
    else:
        form = SuggestionTicketForm()
    return render(request, "core/suggestion_form.html", {"form": form})


@login_required
def suggestion_detail(request, pk):
    ticket = get_ticket(pk)
    if not ticket:
        raise Http404("Suggestion not found")

    if request.method == "POST":
        form = SuggestionReplyForm(request.POST)
        selected_status = request.POST.get("status") if request.user.is_staff or request.user.is_superuser else None
        if form.is_valid():
            message = (form.cleaned_data.get("message") or "").strip()
            if message or selected_status:
                add_reply(ticket.id, message, request.user, status=selected_status)
                messages.success(request, "Reply saved.")
                return redirect("core:suggestion_detail", pk=ticket.id)
            messages.error(request, "Reply or status change is required.")
    else:
        form = SuggestionReplyForm()

    ticket = get_ticket(pk)
    return render(request, "core/suggestion_detail.html", {
        "ticket": ticket,
        "form": form,
        "status_choices": STATUS_CHOICES,
    })


@login_required
@require_POST
def suggestion_status_update(request, pk):
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden("Not allowed")
    new_status = request.POST.get("status")
    if new_status not in dict(STATUS_CHOICES):
        return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
    ticket = update_status(pk, new_status)
    if not ticket:
        return JsonResponse({"ok": False, "error": "Suggestion not found."}, status=404)
    return JsonResponse({"ok": True, "status": ticket.status})


@login_required
@require_POST
def suggestion_delete(request, pk):
    ticket = get_ticket(pk)
    if not ticket:
        return JsonResponse(
            {"success": False, "error": "Suggestion not found."},
            status=404,
        )
    can_delete = request.user.is_staff or request.user.is_superuser
    can_delete = can_delete or ticket.user_name_snapshot == request.user.get_username()
    if not can_delete:
        return JsonResponse(
            {"success": False, "error": "Permission denied."},
            status=403,
        )
    if not delete_ticket(pk):
        return JsonResponse(
            {"success": False, "error": "Suggestion not found."},
            status=404,
        )
    return JsonResponse({"success": True})
