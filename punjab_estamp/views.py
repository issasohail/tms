import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from leases.models import Lease
from leases.models_renewal import LeaseRenewal
from punjab_estamp.models import (
    LeaseEStampWorkflow,
    PunjabEStampDistrict,
    PunjabEStampRelation,
    PunjabEStampTehsil,
)
from punjab_estamp.services.workflow import (
    mark_invalid,
    missing_configuration,
    mark_paid,
    mark_stamp_issued,
    prepare_replacement,
    prepare_workflow,
    record_challan,
    snapshot_components,
    workflow_card_context,
)


PUNJAB_HOME_URL = (
    "https://es.punjab-zameen.gov.pk/"
    "eStampCitizenPortal/ChallanFormView/AddChallanForWhitePaper"
    "?name=GenerateChallan&agree=true"
)
PUNJAB_RETRIEVAL_URL = (
    "https://es.punjab-zameen.gov.pk/"
    "eStampCitizenPortal/Stamp/StampRetrieval?name=stampretrieval"
)
PUNJAB_DOWNLOAD_URL = (
    "https://es.punjab-zameen.gov.pk/"
    "eStampCitizenPortal/Stamp/SearchChallan?name=whitepaper"
)
LAUNCH_TOKEN_SALT = "punjab-estamp-launch-v1"
LAUNCH_TOKEN_MAX_AGE = 4 * 60 * 60


def _require_lease_change(user):
    if not (user.is_superuser or user.has_perm("leases.change_lease")):
        raise PermissionDenied


def _lease_and_history(lease_id, history_id):
    lease = get_object_or_404(
        Lease.objects.select_related(
            "tenant",
            "unit__property__zila",
            "unit__property__tehsil",
            "unit__property__owner_tenant__relation",
            "unit__property__caretaker_tenant__relation",
        ),
        pk=lease_id,
    )
    history = get_object_or_404(LeaseRenewal, pk=history_id, lease=lease)
    return lease, history


def _agreement_redirect(lease, history):
    url = reverse("leases:edit_clauses", kwargs={"pk": lease.pk})
    return redirect(f"{url}?history={history.pk}")


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("The Punjab portal event is not valid JSON.") from exc


def _normalize_identifier(value):
    return "".join(str(value or "").split()).upper()


def _workflow_json(workflow):
    return {
        "workflow_id": workflow.pk,
        "status": workflow.status,
        "challan_number": workflow.challan_number,
        "psid": workflow.psid,
        "stamp_number": workflow.stamp_number,
    }


@login_required
@require_POST
def prepare(request, lease_id, history_id):
    _require_lease_change(request.user)
    lease, history = _lease_and_history(lease_id, history_id)
    try:
        workflow, created = prepare_workflow(lease, history, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _agreement_redirect(lease, history)
    if workflow.challan_number:
        messages.info(request, "The existing Punjab Challan will be continued.")
    elif created:
        messages.success(
            request,
            "Punjab e-Stamp workflow is ready. Portal launch will be connected in Phase 6.",
        )
    else:
        messages.info(request, "The existing Punjab e-Stamp workflow was reused.")
    return _agreement_redirect(lease, history)


@login_required
@require_POST
def replacement(request, lease_id, history_id):
    _require_lease_change(request.user)
    lease, history = _lease_and_history(lease_id, history_id)
    workflow = get_object_or_404(
        LeaseEStampWorkflow,
        lease=lease,
        lease_history=history,
    )
    try:
        prepare_replacement(workflow, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(
            request,
            "The invalid Challan was archived. A replacement can now be created.",
        )
    return _agreement_redirect(lease, history)


@login_required
@require_POST
def configuration_update(request, lease_id, history_id):
    """Save missing pre-challan e-Stamp configuration without leaving the agreement."""
    _require_lease_change(request.user)
    lease, history = _lease_and_history(lease_id, history_id)
    existing = LeaseEStampWorkflow.objects.filter(lease=lease, lease_history=history).first()
    if existing and existing.challan_number:
        return JsonResponse({"ok": False, "error": "This Challan already exists. Historical applicant data cannot be changed."}, status=409)

    try:
        payload = _json_body(request)
        property_obj = lease.unit.property

        applicant_source = str(
            payload.get("applicant_source") or ""
        ).strip() or None

        applicant, _property_data, _portal_data = snapshot_components(
            lease,
            applicant_source=applicant_source,
        )

        district_id = payload.get("district_id")
        tehsil_id = payload.get("tehsil_id")
        if district_id:
            property_obj.zila = get_object_or_404(PunjabEStampDistrict, pk=district_id, active=True)
        if tehsil_id:
            tehsil = get_object_or_404(PunjabEStampTehsil, pk=tehsil_id, active=True)
            if property_obj.zila_id and tehsil.district_id != property_obj.zila_id:
                raise ValidationError("The selected Tehsil does not belong to the selected District.")
            property_obj.tehsil = tehsil
        property_obj.full_clean(exclude=None)
        property_obj.save()

        record_id = applicant.get("record_id")
        if record_id:
            from tenants.models import Tenant
            tenant = get_object_or_404(Tenant, pk=record_id)
            if "name" in payload:
                tenant.first_name = str(payload.get("name") or "").strip()
            if "relation_person_name" in payload:
                tenant.last_name = str(payload.get("relation_person_name") or "").strip()
            if "cnic" in payload:
                tenant.cnic = str(payload.get("cnic") or "").strip()
            if "phone" in payload:
                tenant.phone = str(payload.get("phone") or "").strip()
            if "email" in payload:
                email = str(payload.get("email") or "").strip()
                if email:
                    validate_email(email)
                tenant.email = email or None
            if "address" in payload:
                address = str(payload.get("address") or "").strip()
                tenant.address = address
                tenant.permanent_address = address
            if payload.get("relation_id"):
                tenant.relation = get_object_or_404(PunjabEStampRelation, pk=payload["relation_id"], active=True)
            tenant.full_clean()
            tenant.save()
        elif any(payload.get(key) for key in ("name", "cnic", "phone", "email", "relation_id", "relation_person_name", "address")):
            raise ValidationError("Select an Owner or Caretaker Tenant on the Property before entering applicant identity.")

        lease, _history = _lease_and_history(lease_id, history_id)
        remaining = missing_configuration(
            lease,
            applicant_source=applicant_source,
        )
        applicant_after, property_after, _portal_after = snapshot_components(
            lease,
            applicant_source=applicant_source,
        )
        return JsonResponse({
            "ok": not bool(remaining),
            "saved": True,
            "remaining": remaining,
            "applicant": {"source_label": applicant_after.get("source_label"), "name": applicant_after.get("name"), "record_id": applicant_after.get("record_id")},
            "property": {"property_name": property_after.get("property_name"), "unit_number": property_after.get("unit_number")},
        })
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=422)


@login_required
@require_POST
def launch(request, lease_id, history_id):
    """Return an in-memory browser-helper configuration; never submit Punjab."""
    _require_lease_change(request.user)
    lease, history = _lease_and_history(lease_id, history_id)

    try:
        # Browser launches send JSON so the Owner/Caretaker toggle can be
        # supplied. Keep normal/legacy empty POST requests compatible too.
        payload = (
            _json_body(request)
            if request.content_type == "application/json"
            else {}
        )
        applicant_source = str(
            payload.get("applicant_source") or ""
        ).strip() or None

        workflow, _created = prepare_workflow(
            lease,
            history,
            request.user,
            applicant_source=applicant_source,
        )
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=422)

    if workflow.status in {workflow.STATUS_INVALID, workflow.STATUS_FAILED}:
        return JsonResponse(
            {
                "ok": False,
                "error": "This Challan is invalid. Create a replacement before continuing.",
            },
            status=409,
        )
    if workflow.status == workflow.STATUS_UPLOADED:
        return JsonResponse(
            {"ok": False, "error": "This e-Stamp is already uploaded to TMS."},
            status=409,
        )

    if workflow.challan_number:
        applicant = workflow.applicant_snapshot
        property_data = workflow.property_snapshot
        portal_data = workflow.portal_snapshot
        launch_token = ""
    else:
        applicant, property_data, portal_data = snapshot_components(
            lease,
            workflow.purpose,
            applicant_source=applicant_source,
        )
        launch_token = signing.dumps(
            {
                "workflow_id": workflow.pk,
                "applicant": applicant,
                "property": property_data,
                "portal": portal_data,
            },
            salt=LAUNCH_TOKEN_SALT,
            compress=True,
        )

    if workflow.status == workflow.STATUS_STAMP_ISSUED:
        stage = "download"
        portal_url = PUNJAB_DOWNLOAD_URL
    elif workflow.challan_number:
        stage = "retrieval"
        portal_url = PUNJAB_RETRIEVAL_URL
    else:
        stage = "challan"
        portal_url = PUNJAB_HOME_URL

    return JsonResponse(
        {
            "ok": True,
            "portal_url": portal_url,
            "flow": {
                "workflowId": workflow.pk,
                "leaseId": lease.pk,
                "historyId": history.pk,
                "stage": stage,
                "dryRun": True,
                "applicant": applicant,
                "property": property_data,
                "portal": portal_data,
                "challan": workflow.challan_number,
                "psid": workflow.psid,
                "stampNumber": workflow.stamp_number,
                "launchToken": launch_token,
                "eventUrl": reverse(
                    "punjab_estamp:event", kwargs={"workflow_id": workflow.pk}
                ),
                "uploadUrl": reverse(
                    "leases:lease_file_upload", kwargs={"lease_id": lease.pk}
                ),
                "downloadUrl": PUNJAB_DOWNLOAD_URL,
                "retrievalUrl": PUNJAB_RETRIEVAL_URL,
            },
        }
    )


@login_required
@require_POST
def event(request, workflow_id):
    """Accept non-secret portal milestones relayed by the authenticated TMS tab."""
    _require_lease_change(request.user)
    workflow = get_object_or_404(
        LeaseEStampWorkflow.objects.select_related("lease", "lease_history"),
        pk=workflow_id,
    )
    try:
        payload = _json_body(request)
        action = payload.get("action")
        if action == "challan_generated":
            signed_snapshot = signing.loads(
                payload.get("launch_token") or "",
                salt=LAUNCH_TOKEN_SALT,
                max_age=LAUNCH_TOKEN_MAX_AGE,
            )
            if signed_snapshot.get("workflow_id") != workflow.pk:
                raise ValidationError("The Punjab launch token belongs to another workflow.")
            workflow, _changed = record_challan(
                workflow,
                payload.get("challan_number"),
                payload.get("psid"),
                request.user,
                snapshot={
                    "applicant": signed_snapshot.get("applicant") or {},
                    "property": signed_snapshot.get("property") or {},
                    "portal": signed_snapshot.get("portal") or {},
                },
            )
        elif action == "stamp_found":
            if _normalize_identifier(payload.get("challan_number")) != _normalize_identifier(
                workflow.challan_number
            ):
                raise ValidationError("The Punjab result does not exactly match this Challan.")
            if workflow.status == workflow.STATUS_CHALLAN_GENERATED:
                workflow, _changed = mark_paid(workflow, request.user)
            workflow, _changed = mark_stamp_issued(
                workflow, payload.get("stamp_number"), request.user
            )
        elif action == "challan_invalid":
            workflow = mark_invalid(
                workflow, payload.get("error") or "Punjab reported the Challan as invalid.", request.user
            )
        elif action == "portal_error":
            workflow.last_checked_at = timezone.now()
            workflow.last_portal_error = str(payload.get("error") or "")[:2000]
            workflow.updated_by = request.user
            workflow.save(
                update_fields=[
                    "last_checked_at",
                    "last_portal_error",
                    "updated_by",
                    "updated_at",
                ]
            )
        else:
            raise ValidationError("Unknown Punjab portal event.")
    except signing.BadSignature:
        return JsonResponse(
            {"ok": False, "error": "The Punjab launch session expired or is invalid."},
            status=422,
        )
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": " ".join(exc.messages)}, status=422)
    return JsonResponse({"ok": True, "workflow": _workflow_json(workflow)})


@login_required
@require_GET
def state(request, lease_id, history_id):
    _require_lease_change(request.user)
    lease, history = _lease_and_history(lease_id, history_id)
    card = workflow_card_context(lease, history)
    workflow = card["workflow"]
    return JsonResponse(
        {
            "workflow_id": workflow.pk if workflow else None,
            "lease_id": lease.pk,
            "lease_history_id": history.pk,
            "status": card["state"],
            "status_label": card["status_label"],
            "challan_number": workflow.challan_number if workflow else "",
            "psid": workflow.psid if workflow else "",
            "stamp_number": workflow.stamp_number if workflow else "",
            "applicant": card["applicant"],
            "property": card["property"],
            "portal": card["portal"],
            "missing_configuration": card["missing"],
        }
    )
