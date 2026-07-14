import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_whatsapp_ai_message_task(self, message_log_id):
    from whatsapp.models import WhatsAppMessageLog
    from whatsapp.services.whatsapp_ai import process_inbound_whatsapp_message

    message_log = WhatsAppMessageLog.objects.get(pk=message_log_id)
    process_inbound_whatsapp_message(message_log)
    logger.info("Processed WhatsApp AI message %s through Celery", message_log_id)
