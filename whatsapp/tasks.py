import logging
import os
import tempfile

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_whatsapp_ai_message_task(self, message_log_id):
    from whatsapp.models import WhatsAppMessageLog
    from whatsapp.services.whatsapp_ai import process_inbound_whatsapp_message

    message_log = WhatsAppMessageLog.objects.get(pk=message_log_id)
    process_inbound_whatsapp_message(message_log)
    logger.info("Processed WhatsApp AI message %s through Celery", message_log_id)


def download_pending_media(pending_media_id):
    """Fetch the actual bytes for a video/audio PendingWhatsAppMedia row in the
    background, so the webhook reply for that message never has to wait on a
    slow WhatsApp CDN download. See media_processor.create_pending_media, which
    creates the row immediately with processing=True and defers this call."""
    from django.conf import settings
    from django.core.files import File

    from whatsapp.models import PendingWhatsAppMedia
    from whatsapp.services.whatsapp import (
        WhatsAppMediaTooLargeError,
        WhatsAppService,
    )

    pending = PendingWhatsAppMedia.objects.filter(pk=pending_media_id).first()
    if not pending:
        logger.warning("download_pending_media_task: pending media %s no longer exists", pending_media_id)
        return
    try:
        if not pending.whatsapp_media_id:
            pending.processing = False
            pending.ai_notes = f"{pending.ai_notes} No WhatsApp media id was available to download.".strip()
            pending.save(update_fields=["processing", "ai_notes", "updated_at"])
            return

        filename = pending.original_filename or f"whatsapp-{pending.whatsapp_media_id}.bin"
        max_bytes = int(
            getattr(
                settings,
                "WHATSAPP_MAX_INBOUND_VIDEO_BYTES"
                if pending.media_type == "video"
                else "WHATSAPP_MAX_INBOUND_MEDIA_BYTES",
                250 * 1024 * 1024
                if pending.media_type == "video"
                else 16 * 1024 * 1024,
            )
        )
        temporary_path = ""
        try:
            suffix = os.path.splitext(filename)[1][:12]
            with tempfile.NamedTemporaryFile(
                prefix="tms-whatsapp-",
                suffix=suffix,
                delete=False,
            ) as temporary:
                temporary_path = temporary.name
                metadata = WhatsAppService().download_media_to_file(
                    pending.whatsapp_media_id,
                    temporary,
                    max_bytes,
                )
            if not metadata:
                pending.processing = False
                pending.ai_notes = (
                    f"{pending.ai_notes} Background download failed; "
                    "check WhatsApp media token/config."
                ).strip()
                pending.save(update_fields=["processing", "ai_notes", "updated_at"])
                return
            with open(temporary_path, "rb") as downloaded:
                pending.file.save(filename, File(downloaded), save=False)
            downloaded_size = int(metadata.get("downloaded_size") or 0)
            size_note = f"Downloaded WhatsApp media size: {downloaded_size / (1024 * 1024):.2f} MiB."
            if size_note not in pending.ai_notes:
                pending.ai_notes = f"{pending.ai_notes} {size_note}".strip()
        except WhatsAppMediaTooLargeError as exc:
            pending.processing = False
            media_label = (pending.media_type or "media").title()
            pending.ai_notes = (
                f"{pending.ai_notes} {media_label} is {exc.actual_bytes / (1024 * 1024):.2f} MiB "
                f"and exceeds the TMS safety limit of {exc.max_bytes / (1024 * 1024):.0f} MiB."
            ).strip()
            pending.save(update_fields=["processing", "ai_notes", "updated_at"])
            return
        finally:
            if temporary_path:
                try:
                    os.remove(temporary_path)
                except FileNotFoundError:
                    pass
        pending.processing = False
        pending.save(update_fields=["file", "processing", "ai_notes", "updated_at"])
        logger.info("Downloaded deferred WhatsApp media for pending media %s", pending_media_id)
    except Exception:
        logger.exception(
            "Unexpected deferred WhatsApp media download failure for pending media %s",
            pending_media_id,
        )
        pending.processing = False
        failure_note = "Background download stopped unexpectedly. Use Retry Download to try again."
        if failure_note not in pending.ai_notes:
            pending.ai_notes = f"{pending.ai_notes} {failure_note}".strip()
        pending.save(update_fields=["processing", "ai_notes", "updated_at"])
        raise


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def download_pending_media_task(self, pending_media_id):
    download_pending_media(pending_media_id)


@shared_task
def process_whatsapp_handover_reminders_task():
    from whatsapp.services.handover.reminders import send_due_handover_reminders

    result = send_due_handover_reminders()
    logger.info("Processed WhatsApp handover reminders: %s", result)
    return result
