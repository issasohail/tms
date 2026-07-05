import logging
import re
from calendar import monthrange
from datetime import timedelta
from typing import Iterable

import requests
from django.conf import settings
from django.utils import timezone

from leases.whatsapp import normalize_whatsapp_phone
from whatsapp.models import (
    WhatsAppConversation,
    WhatsAppExternalLinkToken,
    WhatsAppMessageLog,
    WhatsAppUtilityTemplate,
)

logger = logging.getLogger(__name__)

# Meta template spelling is intentionally different from the local key.
# Do not "correct" invocice_notice: that is the approved Meta template name.
META_TEMPLATE_NAME_OVERRIDES = {
    "invoice_notice": "invocice_notice",
}


class WhatsAppConfigurationError(RuntimeError):
    pass


class WhatsAppService:
    def __init__(self, created_by=None):
        self.created_by = created_by
        self.access_token = settings.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.business_account_id = settings.WHATSAPP_BUSINESS_ACCOUNT_ID
        self.api_version = settings.WHATSAPP_API_VERSION or "v23.0"
        self.timeout = getattr(settings, "WHATSAPP_REQUEST_TIMEOUT", 20)

    @staticmethod
    def normalize_phone_number(phone_number, country_code=None):
        return normalize_whatsapp_phone(
            phone_number,
            country_code=country_code
            or getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "+92"),
        )

    def send_text(self, phone_number, body, **context):
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.normalize_phone_number(phone_number),
            "type": "text",
            "text": {"preview_url": False, "body": body or ""},
        }
        return self._send(
            payload, message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT, **context
        )

    def configuration_status(self):
        missing = []
        if not self.access_token:
            missing.append("WHATSAPP_ACCESS_TOKEN")
        if not self.phone_number_id:
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        if not self.business_account_id:
            missing.append("WHATSAPP_BUSINESS_ACCOUNT_ID")
        return {
            "ok": not missing,
            "missing": missing,
            "api_version": self.api_version,
            "phone_number_id_configured": bool(self.phone_number_id),
            "business_account_id_configured": bool(self.business_account_id),
            "access_token_configured": bool(self.access_token),
        }

    def send_template(
        self,
        phone_number,
        template_name,
        language_code=None,
        components=None,
        body_parameters=None,
        button_parameter=None,
        **context,
    ):
        body_parameters = list(body_parameters or [])
        if components is None:
            components = self._template_components(body_parameters, button_parameter)
        payload = {
            "messaging_product": "whatsapp",
            "to": self.normalize_phone_number(phone_number),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language_code
                    or getattr(settings, "WHATSAPP_DEFAULT_LANGUAGE", "en")
                },
            },
        }
        if components:
            payload["template"]["components"] = components
        return self._send(
            payload,
            message_type=context.pop(
                "message_type", WhatsAppMessageLog.MESSAGE_TYPE_TEMPLATE
            ),
            template_name=template_name,
            body_parameters=body_parameters,
            button_parameter=button_parameter or "",
            **context,
        )

    def send_utility_template(
        self,
        phone_number,
        template_name,
        body_parameters=None,
        button_parameter=None,
        language_code=None,
        **context,
    ):
        template_key = template_name
        configured_template = WhatsAppUtilityTemplate.objects.filter(
            key=template_key
        ).first()
        if configured_template:
            if not configured_template.is_active:
                return self._log_disabled_utility_template(
                    phone_number,
                    configured_template,
                    body_parameters=body_parameters or [],
                    button_parameter=button_parameter or "",
                    **context,
                )
            template_name = (
                META_TEMPLATE_NAME_OVERRIDES.get(configured_template.key)
                or configured_template.template_name
                or configured_template.key
            )
            language_code = language_code or configured_template.language_code
        else:
            template_name = META_TEMPLATE_NAME_OVERRIDES.get(template_key, template_key)
        return self.send_template(
            phone_number,
            template_name,
            language_code=language_code,
            body_parameters=body_parameters or [],
            button_parameter=button_parameter,
            **context,
        )

    def send_document(
        self, phone_number, document_url, filename=None, caption=None, **context
    ):
        message_type = context.pop(
            "message_type", WhatsAppMessageLog.MESSAGE_TYPE_DOCUMENT
        )
        document = {"link": document_url}
        if filename:
            document["filename"] = filename
        if caption:
            document["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "to": self.normalize_phone_number(phone_number),
            "type": "document",
            "document": document,
        }
        return self._send(payload, message_type=message_type, **context)

    def send_document_bytes(
        self,
        phone_number,
        file_bytes,
        filename,
        mime_type="application/pdf",
        caption=None,
        **context,
    ):
        media_result = self._upload_media(file_bytes, filename, mime_type)
        if not media_result.get("ok"):
            return media_result

        message_type = context.pop(
            "message_type", WhatsAppMessageLog.MESSAGE_TYPE_DOCUMENT
        )
        document = {"id": media_result["media_id"]}
        if filename:
            document["filename"] = filename
        if caption:
            document["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "to": self.normalize_phone_number(phone_number),
            "type": "document",
            "document": document,
        }
        return self._send(payload, message_type=message_type, **context)

    def send_image(self, phone_number, image_url, caption=None, **context):
        image = {"link": image_url}
        if caption:
            image["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "to": self.normalize_phone_number(phone_number),
            "type": "image",
            "image": image,
        }
        return self._send(
            payload, message_type=WhatsAppMessageLog.MESSAGE_TYPE_IMAGE, **context
        )

    def send_image_bytes(
        self,
        phone_number,
        image_bytes,
        filename="image.jpg",
        mime_type="image/jpeg",
        caption=None,
        **context,
    ):
        media_result = self._upload_media(image_bytes, filename, mime_type)
        if not media_result.get("ok"):
            return media_result

        image = {"id": media_result["media_id"]}
        if caption:
            image["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "to": self.normalize_phone_number(phone_number),
            "type": "image",
            "image": image,
        }
        return self._send(
            payload, message_type=WhatsAppMessageLog.MESSAGE_TYPE_IMAGE, **context
        )

    def send_pdf(self, phone_number, pdf_url, filename=None, caption=None, **context):
        filename = filename or self._filename_from_url(pdf_url) or "document.pdf"
        return self.send_document(
            phone_number,
            pdf_url,
            filename=filename,
            caption=caption,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_PDF,
            **context,
        )

    def send_pdf_bytes(
        self, phone_number, pdf_bytes, filename=None, caption=None, **context
    ):
        return self.send_document_bytes(
            phone_number,
            pdf_bytes,
            filename or "document.pdf",
            mime_type="application/pdf",
            caption=caption,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_PDF,
            **context,
        )

    def send_invoice(
        self, invoice, phone_number=None, message=None, pdf_bytes=None, filename=None
    ):
        lease = getattr(invoice, "lease", None)
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        if not is_whatsapp_session_open(phone):
            return self.send_invoice_notice_template(invoice, phone_number=phone)
        body = (
            message
            or f"Invoice {getattr(invoice, 'invoice_number', invoice.pk)} is ready."
        )
        if pdf_bytes:
            filename = (
                filename
                or f"Invoice_{getattr(invoice, 'invoice_number', invoice.pk)}.pdf"
            )
            return self.send_pdf_bytes(
                phone,
                pdf_bytes,
                filename=filename,
                caption=body,
                tenant=tenant,
                lease=lease,
                invoice=invoice,
            )
        return self.send_text(phone, body, tenant=tenant, lease=lease, invoice=invoice)

    def send_receipt(
        self, payment, phone_number=None, message=None, pdf_bytes=None, filename=None
    ):
        lease = getattr(payment, "lease", None)
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        if not is_whatsapp_session_open(phone):
            return self.send_payment_confirmation_template(payment, phone_number=phone)
        body = message or f"Payment receipt for Rs. {getattr(payment, 'amount', '')}."
        if pdf_bytes:
            filename = (
                filename or f"payment_receipt_{getattr(payment, 'pk', 'receipt')}.pdf"
            )
            return self.send_pdf_bytes(
                phone,
                pdf_bytes,
                filename=filename,
                caption=body,
                tenant=tenant,
                lease=lease,
                payment=payment,
            )
        return self.send_text(phone, body, tenant=tenant, lease=lease, payment=payment)

    def send_lease(self, lease, document_url=None, message=None):
        tenant = getattr(lease, "tenant", None)
        phone = getattr(tenant, "phone", "")
        if not is_whatsapp_session_open(phone):
            if document_url:
                return self.send_agreement_ready_template(lease, phone_number=phone)
            return self.send_lease_ledger_template(lease, phone_number=phone)
        if document_url:
            return self.send_pdf(
                phone, document_url, caption=message, tenant=tenant, lease=lease
            )
        return self.send_text(
            phone,
            message or "Your lease information is ready.",
            tenant=tenant,
            lease=lease,
        )

    def send_maintenance_update(self, maintenance_request, message=None):
        tenant = getattr(maintenance_request, "tenant", None) or getattr(
            maintenance_request, "source_tenant", None
        )
        phone = getattr(tenant, "phone", "")
        if not is_whatsapp_session_open(phone):
            lease = getattr(maintenance_request, "lease", None)
            return self.send_utility_template(
                phone,
                "maintenance_update",
                body_parameters=[
                    self._tenant_name(tenant),
                    self._property_unit(lease),
                    getattr(
                        maintenance_request,
                        "get_status_display",
                        lambda: getattr(maintenance_request, "status", ""),
                    )(),
                ],
                tenant=tenant,
                lease=lease,
                maintenance_request=maintenance_request,
            )
        body = (
            message
            or f"Maintenance update: {getattr(maintenance_request, 'status', '')}."
        )
        return self.send_text(
            phone, body, tenant=tenant, maintenance_request=maintenance_request
        )

    def send_payment_confirmation(self, payment, phone_number=None, message=None):
        return self.send_receipt(payment, phone_number=phone_number, message=message)

    def send_invoice_notice_template(self, invoice, phone_number=None):
        lease = getattr(invoice, "lease", None)
        tenant = getattr(lease, "tenant", None)
        unit = getattr(lease, "unit", None)
        property_obj = getattr(unit, "property", None) if unit else None

        phone = phone_number or getattr(tenant, "phone", "")

        property_name = (
            getattr(property_obj, "name", "")
            or getattr(property_obj, "property_name", "")
            or str(property_obj or "")
        )
        unit_name = (
            getattr(unit, "unit_number", "")
            or getattr(unit, "name", "")
            or str(unit or "")
        )

        # invoice_notice body variables:
        # {{1}} tenant, {{2}} property, {{3}} unit, {{4}} amount, {{5}} due date.
        return self.send_utility_template(
            phone,
            "invoice_notice",
            body_parameters=[
                self._tenant_name(tenant),
                property_name,
                unit_name,
                self._money(getattr(invoice, "amount", "")),
                self._date(getattr(invoice, "due_date", "")),
            ],
            tenant=tenant,
            lease=lease,
            invoice=invoice,
        )

    def send_payment_confirmation_template(self, payment, phone_number=None):
        lease = getattr(payment, "lease", None)
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        token = self._payment_receipt_button_token(payment)
        return self.send_utility_template(
            phone,
            "payment_confirmation",
            body_parameters=[
                self._tenant_name(tenant),
                self._property_unit(lease),
                self._money(getattr(payment, "amount", "")),
                getattr(payment, "reference_number", "")
                or str(getattr(payment, "pk", "")),
            ],
            button_parameter=token,
            tenant=tenant,
            lease=lease,
            payment=payment,
        )

    def send_balance_reminder_template(self, lease, phone_number=None):
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        link = self._ledger_link(lease, phone)
        return self.send_utility_template(
            phone,
            "balance_reminder",
            body_parameters=[
                self._tenant_name(tenant),
                self._property_unit(lease),
                self._money_amount(self._lease_balance(lease)),
                self._lease_due_date(lease),
            ],
            button_parameter=link.token,
            tenant=tenant,
            lease=lease,
        )

    def send_lease_ledger_template(self, lease, phone_number=None):
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        link = self._ledger_link(lease, phone)
        return self.send_utility_template(
            phone,
            "lease_ledger_link",
            body_parameters=[self._tenant_name(tenant), self._property_unit(lease)],
            button_parameter=link.token,
            tenant=tenant,
            lease=lease,
        )

    def send_rent_due_reminder_template(self, invoice, phone_number=None):
        lease = getattr(invoice, "lease", None)
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        return self.send_utility_template(
            phone,
            "rent_due_reminder",
            body_parameters=[
                self._tenant_name(tenant),
                self._property_unit(lease),
                self._money(getattr(invoice, "amount", "")),
                self._date(getattr(invoice, "due_date", "")),
            ],
            button_parameter=self._invoice_button_token(invoice),
            tenant=tenant,
            lease=lease,
            invoice=invoice,
        )

    def send_late_fee_reminder_template(
        self, invoice, reminder_number, phone_number=None
    ):
        lease = getattr(invoice, "lease", None)
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        due_date = getattr(invoice, "due_date", None)
        days_overdue = ""
        if due_date:
            days_overdue = (timezone.localdate() - due_date).days
        return self.send_utility_template(
            phone,
            "late_fee_reminder",
            body_parameters=[
                self._tenant_name(tenant),
                getattr(invoice, "invoice_number", ""),
                str(reminder_number),
                self._money(getattr(invoice, "amount", "")),
                self._date(due_date),
                str(days_overdue),
            ],
            button_parameter=self._invoice_button_token(invoice),
            tenant=tenant,
            lease=lease,
            invoice=invoice,
        )

    def send_agreement_ready_template(self, lease, phone_number=None):
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        link = self._external_link(
            WhatsAppExternalLinkToken.LINK_AGREEMENT_VIEW,
            lease,
            phone,
            target_model="Lease",
            metadata={"lease_id": getattr(lease, "pk", None)},
        )
        return self.send_utility_template(
            phone,
            "agreement_ready",
            body_parameters=[self._tenant_name(tenant), self._property_unit(lease)],
            button_parameter=link.token,
            tenant=tenant,
            lease=lease,
        )

    def download_media_bytes(self, media_id):
        if not media_id:
            return b""
        try:
            self._validate_config()
            metadata_response = requests.get(
                f"https://graph.facebook.com/{self.api_version}/{media_id}",
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=self.timeout,
            )
            metadata = metadata_response.json() if metadata_response.content else {}
            media_url = metadata.get("url")
            if not metadata_response.ok or not media_url:
                logger.warning("WhatsApp media metadata failed: %s", metadata)
                return b""

            file_response = requests.get(
                media_url,
                headers={"Authorization": f"Bearer {self.access_token}"},
                timeout=self.timeout,
            )
            if file_response.ok:
                return file_response.content
            logger.warning(
                "WhatsApp media download failed: HTTP %s", file_response.status_code
            )
        except (
            WhatsAppConfigurationError,
            requests.RequestException,
            ValueError,
        ) as exc:
            logger.warning("WhatsApp media download unavailable: %s", exc)
        return b""

    def send_bulk(self, recipients: Iterable[dict], body):
        results = []
        for recipient in recipients:
            results.append(
                self.send_text(
                    recipient.get("phone_number") or recipient.get("phone"),
                    body,
                    tenant=recipient.get("tenant"),
                    lease=recipient.get("lease"),
                )
            )
        return results

    def schedule_message(self, phone_number, body, scheduled_for, **context):
        log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
            phone_number=self.normalize_phone_number(phone_number),
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT,
            status=WhatsAppMessageLog.STATUS_SCHEDULED,
            payload={"body": body},
            scheduled_for=scheduled_for,
            created_by=self.created_by,
            **self._model_context(context),
        )
        return {
            "ok": True,
            "scheduled": True,
            "log_id": log.pk,
            "scheduled_for": scheduled_for,
        }

    def retry_failed(self, limit=25):
        logs = WhatsAppMessageLog.objects.filter(
            direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
            status=WhatsAppMessageLog.STATUS_FAILED,
        ).order_by("created_at")[:limit]
        results = []
        for log in logs:
            log.retry_count += 1
            log.status = WhatsAppMessageLog.STATUS_PENDING
            log.save(update_fields=["retry_count", "status", "updated_at"])
            results.append(
                self._send(log.payload, existing_log=log, message_type=log.message_type)
            )
        return results

    def _send(
        self,
        payload,
        message_type,
        existing_log=None,
        template_name="",
        body_parameters=None,
        button_parameter="",
        **context,
    ):
        phone_number = payload.get("to", "")
        log = existing_log or WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
            phone_number=phone_number,
            template_name=template_name,
            message_type=message_type,
            status=WhatsAppMessageLog.STATUS_PENDING,
            body_parameters=body_parameters or [],
            button_parameter=button_parameter or "",
            payload=payload,
            created_by=self.created_by,
            **self._model_context(context),
        )

        if not phone_number:
            return self._mark_failed(log, "Phone number is required.")

        try:
            self._validate_config()
        except WhatsAppConfigurationError as exc:
            return self._mark_failed(log, str(exc))

        try:
            response = requests.post(
                self._messages_url(),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            try:
                response_json = response.json()
            except ValueError:
                response_json = {"text": response.text}

            if response.ok:
                messages = response_json.get("messages") or []
                log.status = WhatsAppMessageLog.STATUS_SENT
                log.api_response = response_json
                if messages:
                    log.wa_message_id = messages[0].get("id", "")
                log.save(
                    update_fields=[
                        "status",
                        "api_response",
                        "wa_message_id",
                        "updated_at",
                    ]
                )
                return {
                    "ok": True,
                    "log_id": log.pk,
                    "message_type": log.message_type,
                    "template_name": log.template_name,
                    "response": response_json,
                    "debug": self._send_debug(log, payload, response_json),
                }

            error_text = self._api_error_text(response_json, response.status_code)
            log.status = WhatsAppMessageLog.STATUS_FAILED
            log.api_response = response_json
            log.error_text = error_text
            log.save(
                update_fields=["status", "api_response", "error_text", "updated_at"]
            )
            logger.warning(
                "WhatsApp API request failed: %s | debug=%s",
                error_text,
                self._send_debug(log, payload, response_json),
            )
            return {
                "ok": False,
                "log_id": log.pk,
                "message_type": log.message_type,
                "template_name": log.template_name,
                "error": error_text,
                "response": response_json,
                "debug": self._send_debug(log, payload, response_json),
            }
        except requests.RequestException as exc:
            logger.warning("WhatsApp API network error: %s", exc)
            return self._mark_failed(log, str(exc))

    def _upload_media(self, file_bytes, filename, mime_type):
        try:
            self._validate_config()
        except WhatsAppConfigurationError as exc:
            return {"ok": False, "error": str(exc)}

        try:
            response = requests.post(
                self._media_url(),
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (filename, file_bytes, mime_type)},
                timeout=self.timeout,
            )
            try:
                response_json = response.json()
            except ValueError:
                response_json = {"text": response.text}

            if response.ok and response_json.get("id"):
                return {
                    "ok": True,
                    "media_id": response_json["id"],
                    "response": response_json,
                }

            return {
                "ok": False,
                "error": self._api_error_text(response_json, response.status_code),
                "response": response_json,
            }
        except requests.RequestException as exc:
            logger.warning("WhatsApp media upload failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def _validate_config(self):
        missing = []
        if not self.access_token:
            missing.append("WHATSAPP_ACCESS_TOKEN")
        if not self.phone_number_id:
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        if missing:
            raise WhatsAppConfigurationError(
                f"Missing WhatsApp setting(s): {', '.join(missing)}"
            )

    def _messages_url(self):
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    def _media_url(self):
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/media"

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _mark_failed(self, log, error_text):
        log.status = WhatsAppMessageLog.STATUS_FAILED
        log.error_text = error_text
        log.save(update_fields=["status", "error_text", "updated_at"])
        return {
            "ok": False,
            "log_id": log.pk,
            "message_type": log.message_type,
            "template_name": log.template_name,
            "error": error_text,
            "debug": self._send_debug(log, log.payload, log.api_response),
        }

    def _log_disabled_utility_template(
        self,
        phone_number,
        template,
        body_parameters=None,
        button_parameter="",
        **context,
    ):
        phone = self.normalize_phone_number(phone_number)
        log = WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
            phone_number=phone,
            template_name=template.template_name or template.key,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEMPLATE,
            status=WhatsAppMessageLog.STATUS_FAILED,
            body_parameters=body_parameters or [],
            button_parameter=button_parameter or "",
            payload={
                "type": "template",
                "template_key": template.key,
                "template_name": template.template_name,
                "language_code": template.language_code,
            },
            error_text="WhatsApp Utility template is inactive in Settings.",
            created_by=self.created_by,
            **self._model_context(context),
        )
        return {
            "ok": False,
            "log_id": log.pk,
            "message_type": log.message_type,
            "template_name": log.template_name,
            "error": log.error_text,
        }

    @staticmethod
    def _api_error_text(response_json, status_code):
        error = (
            response_json.get("error", {}) if isinstance(response_json, dict) else {}
        )
        message = error.get("message") or "WhatsApp API error"
        code = error.get("code")
        subcode = error.get("error_subcode")
        details = (
            error.get("error_data", {}).get("details")
            if isinstance(error.get("error_data"), dict)
            else ""
        )
        text = f"HTTP {status_code}: {message}"
        if code:
            text += f" (code {code})"
        if subcode:
            text += f" (subcode {subcode})"
        if details:
            text += f" - {details}"
        return text

    @staticmethod
    def _send_debug(log, payload, response_json=None):
        template = payload.get("template") or {}
        language = template.get("language") or {}
        components = template.get("components") or []
        body_component = next(
            (item for item in components if item.get("type") == "body"),
            {},
        )
        body_params = body_component.get("parameters") or []
        button_components = [
            item for item in components if item.get("type") == "button"
        ]
        error = (
            response_json.get("error", {})
            if isinstance(response_json, dict)
            else {}
        )
        return {
            "log_id": getattr(log, "pk", None),
            "phone_number": payload.get("to", ""),
            "message_type": payload.get("type", ""),
            "template_name": template.get("name") or getattr(log, "template_name", ""),
            "language_code": language.get("code", ""),
            "body_parameter_count": len(body_params),
            "body_parameters": [
                (param.get("text", "") if isinstance(param, dict) else str(param))
                for param in body_params
            ],
            "button_parameter": getattr(log, "button_parameter", ""),
            "button_component_count": len(button_components),
            "meta_error_code": error.get("code"),
            "meta_error_subcode": error.get("error_subcode"),
            "meta_error_type": error.get("type"),
            "meta_error_details": (
                error.get("error_data", {}).get("details")
                if isinstance(error.get("error_data"), dict)
                else ""
            ),
            "meta_fbtrace_id": error.get("fbtrace_id"),
        }

    @staticmethod
    def _model_context(context):
        allowed = {"tenant", "lease", "invoice", "payment", "maintenance_request"}
        return {
            key: value
            for key, value in context.items()
            if key in allowed and value is not None
        }

    @staticmethod
    def _filename_from_url(url):
        name = (url or "").rstrip("/").split("/")[-1]
        return name if name.lower().endswith(".pdf") else ""

    @staticmethod
    def _template_components(body_parameters, button_parameter):
        components = []
        if body_parameters:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        # Meta rejects blank/whitespace-only parameter text with
                        # 400 (#131008) "Required parameter is missing", so fall
                        # back to a placeholder rather than sending "".
                        {"type": "text", "text": str(value).strip() if str(value or "").strip() else "-"}
                        for value in body_parameters
                    ],
                }
            )
        if button_parameter:
            components.append(
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [{"type": "text", "text": str(button_parameter)}],
                }
            )
        return components

    @staticmethod
    def _tenant_name(tenant):
        if not tenant:
            return "Tenant"
        if hasattr(tenant, "get_full_name"):
            return tenant.get_full_name() or "Tenant"
        return str(tenant) or "Tenant"

    @staticmethod
    def _property_unit(lease):
        unit = getattr(lease, "unit", None)
        property_obj = getattr(unit, "property", None)
        property_name = getattr(property_obj, "property_name", "") or ""
        unit_number = getattr(unit, "unit_number", "") or ""
        return " / ".join(part for part in [property_name, unit_number] if part) or "-"

    @staticmethod
    def _date(value):
        if hasattr(value, "strftime"):
            return value.strftime("%b %d, %Y")
        return str(value or "")

    @staticmethod
    def _money(value):
        if value in {None, ""}:
            return "Rs. 0.00"
        try:
            return f"Rs. {float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _money_amount(value):
        if value in {None, ""}:
            return "0.00"
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _lease_due_date(cls, lease):
        value = getattr(lease, "due_date", "") or ""
        if hasattr(value, "strftime"):
            return cls._date(value)

        match = re.search(r"\d{1,2}", str(value))
        if not match:
            return str(value)

        day = int(match.group())
        if day < 1:
            return str(value)

        today = timezone.localdate()
        year = today.year
        month = today.month
        max_day = monthrange(year, month)[1]
        due_day = min(day, max_day)
        due_date = today.replace(day=due_day)

        if due_date < today:
            month = 1 if today.month == 12 else today.month + 1
            year = today.year + 1 if today.month == 12 else today.year
            max_day = monthrange(year, month)[1]
            due_date = due_date.replace(year=year, month=month, day=min(day, max_day))

        return cls._date(due_date)

    @staticmethod
    def _lease_balance(lease):
        balance = getattr(lease, "get_balance", 0)
        return balance() if callable(balance) else balance

    @staticmethod
    def _invoice_button_token(invoice):
        from invoices.public_links import make_public_invoice_token

        return make_public_invoice_token(invoice.pk)

    @staticmethod
    def _payment_receipt_button_token(payment):
        from payments.public_links import make_public_payment_receipt_token

        return make_public_payment_receipt_token(payment.pk)

    def _ledger_link(self, lease, phone):
        return self._external_link(
            WhatsAppExternalLinkToken.LINK_LEDGER_VIEW,
            lease,
            phone,
            target_model="Lease",
            metadata={"lease_id": getattr(lease, "pk", None)},
        )

    def _external_link(self, link_type, lease, phone, target_model="", metadata=None):
        tenant = getattr(lease, "tenant", None)
        return WhatsAppExternalLinkToken.objects.create(
            link_type=link_type,
            phone_number=phone or getattr(tenant, "phone", "") or "",
            tenant=tenant,
            staff_user=self.created_by
            if getattr(self.created_by, "is_authenticated", False)
            else None,
            target_app_label="leases",
            target_model=target_model,
            target_object_id=getattr(lease, "pk", None),
            metadata=metadata or {},
            expires_at=timezone.now() + timedelta(days=7),
        )


def is_whatsapp_session_open(phone_or_tenant):
    phone = getattr(phone_or_tenant, "phone", phone_or_tenant) or ""
    phone = WhatsAppService.normalize_phone_number(phone)
    if not phone:
        return False

    cutoff = timezone.now() - timedelta(hours=24)
    conversation = WhatsAppConversation.objects.filter(phone_number=phone).first()
    if conversation and conversation.last_inbound_message_at:
        return conversation.last_inbound_message_at >= cutoff

    return WhatsAppMessageLog.objects.filter(
        direction=WhatsAppMessageLog.DIRECTION_INBOUND,
        phone_number=phone,
        created_at__gte=cutoff,
    ).exists()


def send_whatsapp_template(
    recipient_phone,
    template_name,
    language_code=None,
    body_parameters=None,
    button_parameter=None,
    **context,
):
    return WhatsAppService().send_utility_template(
        recipient_phone,
        template_name,
        language_code=language_code,
        body_parameters=body_parameters or [],
        button_parameter=button_parameter,
        **context,
    )
