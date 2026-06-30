import logging
from typing import Iterable

import requests
from django.conf import settings

from leases.whatsapp import normalize_whatsapp_phone
from whatsapp.models import WhatsAppMessageLog

logger = logging.getLogger(__name__)


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
            country_code=country_code or getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "+92"),
        )

    def send_text(self, phone_number, body, **context):
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.normalize_phone_number(phone_number),
            "type": "text",
            "text": {"preview_url": False, "body": body or ""},
        }
        return self._send(payload, message_type=WhatsAppMessageLog.MESSAGE_TYPE_TEXT, **context)

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

    def send_template(self, phone_number, template_name, language_code="en_US", components=None, **context):
        payload = {
            "messaging_product": "whatsapp",
            "to": self.normalize_phone_number(phone_number),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components
        return self._send(
            payload,
            message_type=context.pop("message_type", WhatsAppMessageLog.MESSAGE_TYPE_TEMPLATE),
            template_name=template_name,
            **context,
        )

    def send_document(self, phone_number, document_url, filename=None, caption=None, **context):
        message_type = context.pop("message_type", WhatsAppMessageLog.MESSAGE_TYPE_DOCUMENT)
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

    def send_document_bytes(self, phone_number, file_bytes, filename, mime_type="application/pdf", caption=None, **context):
        media_result = self._upload_media(file_bytes, filename, mime_type)
        if not media_result.get("ok"):
            return media_result

        message_type = context.pop("message_type", WhatsAppMessageLog.MESSAGE_TYPE_DOCUMENT)
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
        return self._send(payload, message_type=WhatsAppMessageLog.MESSAGE_TYPE_IMAGE, **context)

    def send_image_bytes(self, phone_number, image_bytes, filename="image.jpg", mime_type="image/jpeg", caption=None, **context):
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
        return self._send(payload, message_type=WhatsAppMessageLog.MESSAGE_TYPE_IMAGE, **context)

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

    def send_pdf_bytes(self, phone_number, pdf_bytes, filename=None, caption=None, **context):
        return self.send_document_bytes(
            phone_number,
            pdf_bytes,
            filename or "document.pdf",
            mime_type="application/pdf",
            caption=caption,
            message_type=WhatsAppMessageLog.MESSAGE_TYPE_PDF,
            **context,
        )

    def send_invoice(self, invoice, phone_number=None, message=None, pdf_bytes=None, filename=None):
        lease = getattr(invoice, "lease", None)
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        body = message or f"Invoice {getattr(invoice, 'invoice_number', invoice.pk)} is ready."
        if pdf_bytes:
            filename = filename or f"Invoice_{getattr(invoice, 'invoice_number', invoice.pk)}.pdf"
            return self.send_pdf_bytes(phone, pdf_bytes, filename=filename, caption=body, tenant=tenant, lease=lease, invoice=invoice)
        return self.send_text(phone, body, tenant=tenant, lease=lease, invoice=invoice)

    def send_receipt(self, payment, phone_number=None, message=None, pdf_bytes=None, filename=None):
        lease = getattr(payment, "lease", None)
        tenant = getattr(lease, "tenant", None)
        phone = phone_number or getattr(tenant, "phone", "")
        body = message or f"Payment receipt for Rs. {getattr(payment, 'amount', '')}."
        if pdf_bytes:
            filename = filename or f"payment_receipt_{getattr(payment, 'pk', 'receipt')}.pdf"
            return self.send_pdf_bytes(phone, pdf_bytes, filename=filename, caption=body, tenant=tenant, lease=lease, payment=payment)
        return self.send_text(phone, body, tenant=tenant, lease=lease, payment=payment)

    def send_lease(self, lease, document_url=None, message=None):
        tenant = getattr(lease, "tenant", None)
        phone = getattr(tenant, "phone", "")
        if document_url:
            return self.send_pdf(phone, document_url, caption=message, tenant=tenant, lease=lease)
        return self.send_text(phone, message or "Your lease information is ready.", tenant=tenant, lease=lease)

    def send_maintenance_update(self, maintenance_request, message=None):
        tenant = getattr(maintenance_request, "tenant", None) or getattr(maintenance_request, "source_tenant", None)
        phone = getattr(tenant, "phone", "")
        body = message or f"Maintenance update: {getattr(maintenance_request, 'status', '')}."
        return self.send_text(phone, body, tenant=tenant, maintenance_request=maintenance_request)

    def send_payment_confirmation(self, payment, phone_number=None, message=None):
        return self.send_receipt(payment, phone_number=phone_number, message=message)

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
            logger.warning("WhatsApp media download failed: HTTP %s", file_response.status_code)
        except (WhatsAppConfigurationError, requests.RequestException, ValueError) as exc:
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
        return {"ok": True, "scheduled": True, "log_id": log.pk, "scheduled_for": scheduled_for}

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
            results.append(self._send(log.payload, existing_log=log, message_type=log.message_type))
        return results

    def _send(self, payload, message_type, existing_log=None, template_name="", **context):
        phone_number = payload.get("to", "")
        log = existing_log or WhatsAppMessageLog.objects.create(
            direction=WhatsAppMessageLog.DIRECTION_OUTBOUND,
            phone_number=phone_number,
            template_name=template_name,
            message_type=message_type,
            status=WhatsAppMessageLog.STATUS_PENDING,
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
                log.save(update_fields=["status", "api_response", "wa_message_id", "updated_at"])
                return {"ok": True, "log_id": log.pk, "response": response_json}

            error_text = self._api_error_text(response_json, response.status_code)
            log.status = WhatsAppMessageLog.STATUS_FAILED
            log.api_response = response_json
            log.error_text = error_text
            log.save(update_fields=["status", "api_response", "error_text", "updated_at"])
            logger.warning("WhatsApp API request failed: %s", error_text)
            return {"ok": False, "log_id": log.pk, "error": error_text, "response": response_json}
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
                return {"ok": True, "media_id": response_json["id"], "response": response_json}

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
            raise WhatsAppConfigurationError(f"Missing WhatsApp setting(s): {', '.join(missing)}")

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
        return {"ok": False, "log_id": log.pk, "error": error_text}

    @staticmethod
    def _api_error_text(response_json, status_code):
        error = response_json.get("error", {}) if isinstance(response_json, dict) else {}
        message = error.get("message") or "WhatsApp API error"
        code = error.get("code")
        return f"HTTP {status_code}: {message}" + (f" (code {code})" if code else "")

    @staticmethod
    def _model_context(context):
        allowed = {"tenant", "lease", "invoice", "payment", "maintenance_request"}
        return {key: value for key, value in context.items() if key in allowed and value is not None}

    @staticmethod
    def _filename_from_url(url):
        name = (url or "").rstrip("/").split("/")[-1]
        return name if name.lower().endswith(".pdf") else ""
