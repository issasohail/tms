from __future__ import annotations

import hashlib
import logging
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from core.models import GlobalSettings
from core.public_urls import build_public_url
from whatsapp.models import WhatsAppMessageLog
from whatsapp.services.identity.phone_normalizer import phone_matches, searchable_suffix
from whatsapp.services.whatsapp import WhatsAppService


PASSWORD_RESET_REQUEST_TEXT = "I forgot my password and I would like to change it."
PASSWORD_RESET_TOKEN_SALT = "accounts.whatsapp-password-reset"
PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS = 20 * 60
PASSWORD_RESET_RATE_LIMIT = 3
PASSWORD_RESET_RATE_WINDOW_SECONDS = 15 * 60

security_logger = logging.getLogger("security.accounts")


def _normalized_request_text(value):
    return " ".join(str(value or "").strip().casefold().split()).rstrip(".!?")


def is_whatsapp_password_reset_request(message):
    if not isinstance(message, dict) or message.get("type") != "text":
        return False
    body = (message.get("text") or {}).get("body", "")
    return _normalized_request_text(body) == _normalized_request_text(PASSWORD_RESET_REQUEST_TEXT)


def whatsapp_password_reset_request_url():
    configured_number = getattr(settings, "MARKETING_WHATSAPP_NUMBER", "")
    if not configured_number:
        try:
            configured_number = (
                GlobalSettings.objects.filter(pk=1)
                .values_list("whatsapp_number", flat=True)
                .first()
                or ""
            )
        except DatabaseError:
            configured_number = ""
    digits = WhatsAppService.normalize_phone_number(configured_number)
    encoded_message = quote(PASSWORD_RESET_REQUEST_TEXT)
    if digits:
        return f"https://wa.me/{digits}?text={encoded_message}"
    return f"https://wa.me/?text={encoded_message}"


def _password_fingerprint(user):
    return salted_hmac(PASSWORD_RESET_TOKEN_SALT, user.password).hexdigest()


def create_whatsapp_password_reset_token(user):
    return signing.dumps(
        {"uid": user.pk, "fp": _password_fingerprint(user)},
        salt=PASSWORD_RESET_TOKEN_SALT,
        compress=True,
    )


def resolve_whatsapp_password_reset_token(token, *, for_update=False):
    try:
        payload = signing.loads(
            token,
            salt=PASSWORD_RESET_TOKEN_SALT,
            max_age=PASSWORD_RESET_TOKEN_MAX_AGE_SECONDS,
        )
        users = get_user_model().objects
        if for_update:
            users = users.select_for_update()
        user = users.get(pk=payload.get("uid"), is_active=True)
    except (
        KeyError,
        TypeError,
        ValueError,
        signing.BadSignature,
        signing.SignatureExpired,
        get_user_model().DoesNotExist,
    ):
        return None
    if not constant_time_compare(str(payload.get("fp", "")), _password_fingerprint(user)):
        return None
    return user


def _matching_active_accounts(phone_number):
    suffix = searchable_suffix(phone_number)
    if not suffix:
        return []
    candidates = (
        get_user_model()
        .objects.filter(is_active=True, whatsapp_number__icontains=suffix)
        .exclude(whatsapp_number="")
    )
    return [user for user in candidates if phone_matches(phone_number, user.whatsapp_number)]


def _rate_allowed(phone_number):
    digest = hashlib.sha256(str(phone_number or "").encode("utf-8")).hexdigest()
    key = f"accounts:whatsapp-password-reset:{digest}"
    if cache.add(key, 1, PASSWORD_RESET_RATE_WINDOW_SECONDS):
        return True
    try:
        return cache.incr(key) <= PASSWORD_RESET_RATE_LIMIT
    except ValueError:
        cache.set(key, 1, PASSWORD_RESET_RATE_WINDOW_SECONDS)
        return True


def _redact_reset_link_log(result):
    log_id = (result or {}).get("log_id")
    if not log_id:
        return
    WhatsAppMessageLog.objects.filter(pk=log_id).update(
        payload={
            "messaging_product": "whatsapp",
            "type": "text",
            "text": {"body": "[Password reset link redacted]"},
        }
    )


def handle_whatsapp_password_reset_request(message_log):
    if not is_whatsapp_password_reset_request(message_log.payload):
        return False

    with transaction.atomic():
        locked_message = WhatsAppMessageLog.objects.select_for_update().get(pk=message_log.pk)
        reset_processing = dict((locked_message.api_response or {}).get("password_reset") or {})
        if reset_processing.get("state") in {"processing", "complete"}:
            return True
        api_response = dict(locked_message.api_response or {})
        api_response["password_reset"] = {
            "state": "processing",
            "started_at": timezone.now().isoformat(),
        }
        locked_message.api_response = api_response
        locked_message.save(update_fields=["api_response", "updated_at"])

    matched_accounts = []
    result = None
    outcome = "unverified"
    try:
        if not _rate_allowed(locked_message.phone_number):
            outcome = "rate_limited"
            response_text = "Too many password reset requests. Please wait 15 minutes and try again."
        else:
            matched_accounts = _matching_active_accounts(locked_message.phone_number)
            if len(matched_accounts) == 1:
                user = matched_accounts[0]
                token = create_whatsapp_password_reset_token(user)
                reset_url = build_public_url(
                    "accounts:whatsapp_password_reset_confirm",
                    kwargs={"token": token},
                )
                response_text = (
                    "We verified your TMS account. Use this secure link within 20 minutes "
                    "to enter your new password twice:\n"
                    f"{reset_url}\n"
                    "If you did not request this, ignore this message."
                )
                outcome = "link_sent"
            else:
                response_text = (
                    "We could not verify one active TMS account for this WhatsApp number. "
                    "Please contact an administrator."
                )

        result = WhatsAppService().send_text(locked_message.phone_number, response_text)
        _redact_reset_link_log(result)
        security_logger.info(
            "WhatsApp password reset request outcome=%s account_count=%s delivery_ok=%s",
            outcome,
            len(matched_accounts),
            bool((result or {}).get("ok")),
        )
    finally:
        locked_message.refresh_from_db(fields=["api_response"])
        api_response = dict(locked_message.api_response or {})
        api_response["password_reset"] = {
            "state": "complete",
            "outcome": outcome,
            "delivery_ok": bool((result or {}).get("ok")),
            "completed_at": timezone.now().isoformat(),
        }
        locked_message.api_response = api_response
        locked_message.save(update_fields=["api_response", "updated_at"])
    return True
