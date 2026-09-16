"""One-time browser pairing and narrowly scoped device API for the Windows helper."""
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from accounts.access import restrict_queryset_to_properties
from properties.models import Unit
from .models import IescoHelperDevice, IescoHelperPairing, IescoStandaloneMeter
from .services_iesco import save_bill_payload


def _digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _can_manage(user):
    return user.is_active and (user.is_superuser or user.has_perm("invoices.change_iescobillreading"))


def _refs_for(user):
    units = restrict_queryset_to_properties(
        Unit.objects.filter(iesco_bill_active=True, electric_meter_num__regex=r"^[0-9]{14}$")
        .exclude(electric_meter_num="00000000000000"), user, "property"
    )
    refs = set(units.values_list("electric_meter_num", flat=True))
    from accounts.access import has_all_property_access
    if has_all_property_access(user):
        refs.update(IescoStandaloneMeter.objects.filter(is_active=True).values_list("reference_no", flat=True))
    return sorted(ref for ref in refs if re.fullmatch(r"[0-9]{14}", ref))


def _device(request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token):
        return None
    digest = _digest(token)
    device = IescoHelperDevice.objects.select_related("paired_by").filter(token_hash=digest).first()
    if not device or not hmac.compare_digest(device.token_hash, digest) or not device.is_active or not _can_manage(device.paired_by):
        return None
    device.last_used_at = timezone.now()
    device.save(update_fields=["last_used_at"])
    return device


def create_pairing_for(user):
    token = secrets.token_urlsafe(48)
    pairing = IescoHelperPairing.objects.create(
        token_hash=_digest(token), requested_by=user,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    return pairing, token


@login_required
@require_POST
def create_pairing(request):
    if not _can_manage(request.user):
        raise PermissionDenied
    if not request.is_secure():
        return JsonResponse({"error": "Open TMS over HTTPS to connect this computer. Pairing is unavailable on a local HTTP page."}, status=400)
    pairing, token = create_pairing_for(request.user)
    response = JsonResponse({"id": pairing.pk, "url": f"tms-iesco://pair?token={token}", "expires_at": pairing.expires_at.isoformat()})
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_GET
def pairing_status(request, pk):
    if not _can_manage(request.user):
        raise PermissionDenied
    pairing = IescoHelperPairing.objects.filter(pk=pk, requested_by=request.user).first()
    if not pairing:
        return JsonResponse({"error": "not found"}, status=404)
    if pairing.status == "pending" and pairing.expires_at <= timezone.now():
        pairing.status = "expired"
        pairing.save(update_fields=["status"])
    status = "revoked" if pairing.device and not pairing.device.is_active else pairing.status
    return JsonResponse({"status": status, "device": pairing.device.name if pairing.device else ""})


@csrf_exempt
@require_POST
def exchange_pairing(request):
    if not request.is_secure():
        return JsonResponse({"error": "HTTPS required"}, status=400)
    if len(request.body) > 4096:
        return JsonResponse({"error": "invalid request"}, status=400)
    try:
        data = json.loads(request.body)
        token = data["token"]
        device_id = UUID(data["device_id"])
        name = str(data["name"]).strip()[:128]
        version = str(data.get("version", ""))[:32]
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({"error": "invalid request"}, status=400)
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", token) or not name:
        return JsonResponse({"error": "invalid request"}, status=400)
    with transaction.atomic():
        pairing = IescoHelperPairing.objects.select_for_update().select_related("requested_by").filter(token_hash=_digest(token)).first()
        if not pairing or not hmac.compare_digest(pairing.token_hash, _digest(token)):
            return JsonResponse({"error": "invalid pairing token"}, status=401)
        if pairing.used_at:
            return JsonResponse({"error": "pairing already used"}, status=409)
        if pairing.status == "expired" or pairing.expires_at <= timezone.now():
            pairing.status = "expired"
            pairing.save(update_fields=["status"])
            return JsonResponse({"error": "pairing expired"}, status=410)
        if pairing.status != "pending":
            return JsonResponse({"error": "pairing already used"}, status=409)
        if not _can_manage(pairing.requested_by):
            return JsonResponse({"error": "permission revoked"}, status=403)
        device = IescoHelperDevice.objects.select_for_update().filter(device_id=device_id).first()
        if device and device.paired_by_id != pairing.requested_by_id:
            return JsonResponse({"error": "computer already paired to another user"}, status=409)
        credential = secrets.token_urlsafe(48)
        if device:
            device.name, device.token_hash, device.paired_at = name, _digest(credential), timezone.now()
            device.is_active, device.helper_version = True, version
            device.save(update_fields=["name", "token_hash", "paired_at", "is_active", "helper_version"])
        else:
            device = IescoHelperDevice.objects.create(device_id=device_id, name=name, token_hash=_digest(credential), paired_by=pairing.requested_by, helper_version=version)
        pairing.device, pairing.used_at, pairing.status = device, timezone.now(), "paired"
        pairing.save(update_fields=["device", "used_at", "status"])
    response = JsonResponse({"device_token": credential, "device_id": str(device_id)})
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_GET
def active_references(request):
    device = _device(request)
    if not device:
        return JsonResponse({"error": "device revoked or unauthorized"}, status=401)
    return JsonResponse({"references": _refs_for(device.paired_by)})


@csrf_exempt
@require_POST
def ingest(request):
    device = _device(request)
    if not device:
        return JsonResponse({"error": "device revoked or unauthorized"}, status=401)
    if len(request.body) > 64 * 1024:
        return JsonResponse({"error": "payload too large"}, status=413)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid json"}, status=400)
    if not isinstance(payload, dict) or payload.get("reference_no") not in _refs_for(device.paired_by):
        return JsonResponse({"error": "reference is not active"}, status=403)
    try:
        reading, _ = save_bill_payload(payload)
    except ValidationError as exc:
        return JsonResponse({"error": "; ".join(exc.messages)}, status=400)
    return JsonResponse({"status": "ok", "id": reading.pk}, status=201)
