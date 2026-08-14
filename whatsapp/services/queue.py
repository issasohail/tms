import logging
import threading

from django.db import close_old_connections

from whatsapp.services.ai_config import get_whatsapp_ai_config

logger = logging.getLogger(__name__)


def enqueue_whatsapp_ai_message(message_log_id, force=False):
    config = get_whatsapp_ai_config()
    if not config.enabled and not force:
        return "disabled"

    if config.use_celery:
        try:
            from whatsapp.tasks import process_whatsapp_ai_message_task

            process_whatsapp_ai_message_task.delay(message_log_id)
            return "celery"
        except Exception:
            logger.exception("Could not queue WhatsApp AI message %s with Celery; using thread fallback.", message_log_id)

    def runner():
        close_old_connections()
        try:
            from whatsapp.models import WhatsAppMessageLog
            from whatsapp.services.whatsapp_ai import process_inbound_whatsapp_message

            message_log = WhatsAppMessageLog.objects.get(pk=message_log_id)
            process_inbound_whatsapp_message(message_log)
        except Exception:
            logger.exception("Failed to process WhatsApp AI message %s", message_log_id)
        finally:
            close_old_connections()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return "thread"


def enqueue_pending_media_download(pending_media_id):
    config = get_whatsapp_ai_config()
    if config.use_celery:
        try:
            from whatsapp.tasks import download_pending_media_task

            download_pending_media_task.delay(pending_media_id)
            return "celery"
        except Exception:
            logger.exception(
                "Could not queue WhatsApp media download %s with Celery; using thread fallback.",
                pending_media_id,
            )

    def runner():
        close_old_connections()
        try:
            from whatsapp.tasks import download_pending_media

            download_pending_media(pending_media_id)
        except Exception:
            logger.exception(
                "Failed to download deferred WhatsApp media %s",
                pending_media_id,
            )
        finally:
            close_old_connections()

    # Keep the Gunicorn worker alive until the media file has either been
    # stored or marked failed. A daemon thread can be terminated during a
    # worker restart and leave PendingWhatsAppMedia.processing=True forever.
    thread = threading.Thread(target=runner, daemon=False)
    thread.start()
    return "thread"
