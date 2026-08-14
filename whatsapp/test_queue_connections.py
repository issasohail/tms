from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase


class WhatsAppQueueConnectionLifecycleTests(SimpleTestCase):
    def _captured_runner(self, enqueue, object_id):
        with patch("whatsapp.services.queue.threading.Thread") as thread_class:
            enqueue(object_id)
        return thread_class.call_args.kwargs["target"]

    @patch("whatsapp.services.queue.get_whatsapp_ai_config")
    def test_ai_thread_closes_old_connections_before_and_after_work(self, get_config):
        from whatsapp.services.queue import enqueue_whatsapp_ai_message

        get_config.return_value = SimpleNamespace(enabled=True, use_celery=False)
        runner = self._captured_runner(enqueue_whatsapp_ai_message, 42)

        with (
            patch("whatsapp.services.queue.close_old_connections") as close_connections,
            patch("whatsapp.models.WhatsAppMessageLog.objects.get") as get_message,
            patch("whatsapp.services.whatsapp_ai.process_inbound_whatsapp_message"),
        ):
            runner()

        self.assertEqual(close_connections.call_count, 2)
        get_message.assert_called_once_with(pk=42)

    @patch("whatsapp.services.queue.get_whatsapp_ai_config")
    def test_media_thread_closes_old_connections_even_when_work_fails(self, get_config):
        from whatsapp.services.queue import enqueue_pending_media_download

        get_config.return_value = SimpleNamespace(use_celery=False)
        runner = self._captured_runner(enqueue_pending_media_download, 99)

        with (
            patch("whatsapp.services.queue.close_old_connections") as close_connections,
            patch(
                "whatsapp.tasks.download_pending_media",
                side_effect=RuntimeError("download failed"),
            ),
        ):
            runner()

        self.assertEqual(close_connections.call_count, 2)
