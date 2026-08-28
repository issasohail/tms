import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase

from smart_meter.management.commands.meter_listener import (
    ClientHandler,
    DbCommandPoller,
)
from smart_meter.models import LiveReading, Meter, MeterReading


class FakeSocket:
    def __init__(self, recv_result=b"", send_error=None):
        self.recv_result = recv_result
        self.send_error = send_error
        self.sent_frames = []
        self.close_calls = 0

    def settimeout(self, _timeout):
        pass

    def setsockopt(self, *_args):
        pass

    def recv(self, _size):
        result = (
            self.recv_result.pop(0)
            if isinstance(self.recv_result, list)
            else self.recv_result
        )
        if isinstance(result, Exception):
            raise result
        return result

    def shutdown(self, _how):
        pass

    def sendall(self, frame):
        if self.send_error is not None:
            raise self.send_error
        self.sent_frames.append(frame)

    def close(self):
        self.close_calls += 1


class BlockingReceiveSocket(FakeSocket):
    def __init__(self):
        super().__init__()
        self.recv_entered = threading.Event()
        self.release_recv = threading.Event()

    def recv(self, _size):
        self.recv_entered.set()
        self.release_recv.wait(timeout=2)
        return b""

    def shutdown(self, _how):
        self.release_recv.set()


class ClientHandlerConnectionLifecycleTests(SimpleTestCase):
    def make_handler(self, recv_result=b""):
        return ClientHandler(FakeSocket(recv_result), ("127.0.0.1", 12345))

    @patch("smart_meter.management.commands.meter_listener.threading.Thread.start")
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_client_handler_cleans_up_on_normal_exit(self, close_connections, _start):
        with self.assertLogs("smart_meter.listener", level="INFO") as captured:
            self.make_handler().run()

        self.assertGreaterEqual(close_connections.call_count, 2)
        self.assertTrue(any("TCP_CONNECTED" in line for line in captured.output))
        self.assertTrue(
            any(
                "TCP_DISCONNECTED" in line and "reason=eof" in line
                for line in captured.output
            )
        )

    @patch("smart_meter.management.commands.meter_listener.threading.Thread.start")
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_client_handler_cleans_up_when_receive_raises(
        self, close_connections, _start
    ):
        self.make_handler(OSError("simulated disconnect")).run()

        self.assertGreaterEqual(close_connections.call_count, 2)

    @patch("smart_meter.management.commands.meter_listener.threading.Thread.start")
    @patch("smart_meter.management.commands.meter_listener.connection.close")
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_quiet_live_handler_rechecks_age_and_force_closes_on_exit(
        self, close_connections, close_connection, _start
    ):
        self.make_handler([TimeoutError(), b""]).run()

        self.assertGreaterEqual(close_connections.call_count, 3)
        close_connection.assert_called_once_with()

    @patch("smart_meter.management.commands.meter_listener.parse_frame")
    @patch(
        "smart_meter.management.commands.meter_listener.verify_checksum",
        return_value=(True, "standard"),
    )
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_repeated_frame_units_keep_cleanup_bounded(
        self, close_connections, _verify_checksum, parse_frame
    ):
        parse_frame.return_value = None
        handler = self.make_handler()

        for _ in range(125):
            handler.process_frame(b"\x68")

        self.assertEqual(close_connections.call_count, 250)
        self.assertEqual(parse_frame.call_count, 125)

    @patch(
        "smart_meter.management.commands.meter_listener.verify_checksum",
        side_effect=RuntimeError("parse failure"),
    )
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_frame_exception_cannot_bypass_cleanup(
        self, close_connections, _verify_checksum
    ):
        handler = self.make_handler()

        with self.assertRaisesRegex(RuntimeError, "parse failure"):
            handler.process_frame(b"\x68")

        self.assertEqual(close_connections.call_count, 2)

    @patch("smart_meter.management.commands.meter_listener.connection.close")
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_database_error_keeps_tcp_handler_alive_for_next_frame(
        self, close_connections, close_connection
    ):
        handler = self.make_handler()
        frame = b"\x68\x01\x02\x03"

        with (
            patch.object(
                handler,
                "_process_frame",
                side_effect=[DatabaseError("temporary MySQL failure"), None],
            ) as process_frame,
            self.assertLogs("smart_meter.listener", level="ERROR") as captured,
        ):
            handler.process_frame(frame)
            handler.process_frame(frame)

        self.assertTrue(handler.alive)
        self.assertEqual(handler.conn.close_calls, 0)
        self.assertEqual(process_frame.call_count, 2)
        close_connection.assert_called_once_with()
        self.assertEqual(close_connections.call_count, 4)
        self.assertTrue(any("DB_ERROR" in line for line in captured.output))

    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_non_database_error_is_not_swallowed(self, _close_connections):
        handler = self.make_handler()

        with (
            patch.object(
                handler, "_process_frame", side_effect=ValueError("programming failure")
            ),
            self.assertRaisesRegex(ValueError, "programming failure"),
        ):
            handler.process_frame(b"\x68")

        self.assertTrue(handler.alive)
        self.assertEqual(handler.conn.close_calls, 0)

    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_cleanup_wrapper_preserves_existing_frame_pipeline(
        self, _close_connections
    ):
        handler = self.make_handler()
        frame = b"\x68\x01\x02\x03"

        with patch.object(handler, "_process_frame") as process_frame:
            handler.process_frame(frame)

        process_frame.assert_called_once_with(frame)

    @patch("smart_meter.management.commands.meter_listener.threading.Thread.start")
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_expired_frame_reports_transport_failure(
        self, _close_connections, _start
    ):
        handler = self.make_handler()
        transport_q = queue.Queue(maxsize=1)
        handler.enqueue_send(
            b"\x68", expire_at=time.time() - 1, transport_q=transport_q
        )

        handler.tx.put(None)
        handler._sender_loop()

        self.assertEqual(
            transport_q.get_nowait(),
            (False, "frame expired before socket send"),
        )
        self.assertEqual(handler.conn.sent_frames, [])

    @patch("smart_meter.management.commands.meter_listener.threading.Thread.start")
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_send_error_reports_transport_failure(self, _close_connections, _start):
        handler = ClientHandler(
            FakeSocket([TimeoutError()], send_error=OSError("simulated send failure")),
            ("127.0.0.1", 12345),
        )
        transport_q = queue.Queue(maxsize=1)
        handler.enqueue_send(b"\x68", transport_q=transport_q)

        handler.tx.put(None)
        handler._sender_loop()

        self.assertEqual(
            transport_q.get_nowait(),
            (False, "socket send failed: simulated send failure"),
        )
        self.assertEqual(handler.disconnect_reason, "send_error")

    @patch("smart_meter.management.commands.meter_listener.threading.Thread.start")
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_legacy_two_tuple_tx_item_still_sends(self, _close_connections, _start):
        handler = self.make_handler()
        handler.tx.put((b"\x68", 0.0))
        handler.tx.put(None)

        handler._sender_loop()

        self.assertEqual(handler.conn.sent_frames, [b"\x68"])

    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_queued_frame_sends_while_receive_is_blocked(self, _close_connections):
        conn = BlockingReceiveSocket()
        handler = ClientHandler(conn, ("127.0.0.1", 12345))
        handler.start()
        self.assertTrue(conn.recv_entered.wait(timeout=1))

        transport_q = queue.Queue(maxsize=1)
        handler.enqueue_send(
            b"\x68",
            expire_at=time.time() + 1,
            transport_q=transport_q,
        )

        self.assertEqual(transport_q.get(timeout=0.5), (True, ""))
        self.assertEqual(conn.sent_frames, [b"\x68"])
        handler.close(reason="test_complete")
        handler.join(timeout=1)
        self.assertFalse(handler.is_alive())

    @patch("smart_meter.management.commands.meter_listener.parse_frame")
    @patch("smart_meter.management.commands.meter_listener._register_handler")
    @patch(
        "smart_meter.management.commands.meter_listener.verify_checksum",
        return_value=(True, "standard"),
    )
    def test_unresolved_di_logs_full_raw_frame(
        self, _verify_checksum, _register_handler, parse_frame
    ):
        parse_frame.return_value = {
            "meter_number": "260305510012",
            "control_code": 0x91,
            "di": None,
            "data": None,
        }
        handler = self.make_handler()
        frame = b"\x68\x01\xAB"

        with self.assertLogs("smart_meter.listener", level="INFO") as captured:
            handler.process_frame(frame)

        raw_log = next(line for line in captured.output if "RAW_UNPARSED_RX" in line)
        self.assertIn("meter=260305510012", raw_log)
        self.assertIn("peer=127.0.0.1:12345", raw_log)
        self.assertIn("control_code=0x91", raw_log)
        self.assertIn("len=3", raw_log)
        self.assertIn("frame=6801AB", raw_log)


class DbCommandPollerConnectionLifecycleTests(TestCase):
    @patch("smart_meter.management.commands.meter_listener.close_old_connections")
    def test_background_poller_closes_connections_after_loop_error(
        self, close_connections
    ):
        poller = DbCommandPoller(interval=0)
        poller._stop = MagicMock()
        poller._stop.is_set.side_effect = [False, True]
        poller._stop.wait.return_value = True

        with patch(
            "smart_meter.management.commands.meter_listener.timezone.now",
            side_effect=RuntimeError("simulated DB work failure"),
        ):
            poller.run()

        self.assertEqual(close_connections.call_count, 2)

    @patch("smart_meter.management.commands.meter_listener.MeterCommand.objects.filter")
    @patch("smart_meter.management.commands.meter_listener._push_waiter")
    @patch("smart_meter.management.commands.meter_listener._get_handler")
    @patch("smart_meter.management.commands.meter_listener.revalidate_command")
    def test_blank_expect_di_does_not_create_wildcard_waiter(
        self, revalidate, get_handler, push_waiter, _filter
    ):
        revalidate.return_value = SimpleNamespace(allowed=True, reason="test")
        handler = MagicMock()

        def fake_enqueue_send(frame, expire_at=None, transport_q=None):
            if transport_q is not None:
                transport_q.put_nowait((True, ""))

        handler.enqueue_send.side_effect = fake_enqueue_send
        get_handler.return_value = handler
        command = SimpleNamespace(
            pk=1,
            meter_number="260305510012",
            expect_di="   ",
            frame_hex="68",
            timeout=0.1,
            attempt_count=0,
        )
        poller = DbCommandPoller(interval=1)

        with patch.object(poller, "_ack") as acknowledge:
            poller._process_command(command)

        push_waiter.assert_not_called()
        handler.enqueue_send.assert_called_once()
        acknowledge.assert_called_once_with(command, "")


class ReadingPersistenceRegressionTests(TestCase):
    def setUp(self):
        self.meter = Meter.objects.create(meter_number="PERSIST-TEST")
        self.handler = ClientHandler(FakeSocket(), ("127.0.0.1", 23456))
        self.parsed = {
            "meter_number": self.meter.meter_number,
            "control_code": 0x91,
            "di": "00000000",
            "data": {
                "total_energy": "123.456",
                "voltage_a": "230.1",
                "current_a": "1.250",
            },
        }

    @patch("smart_meter.management.commands.meter_listener.parse_frame")
    @patch("smart_meter.management.commands.meter_listener._register_handler")
    @patch(
        "smart_meter.management.commands.meter_listener.verify_checksum",
        return_value=(True, "standard"),
    )
    def test_successful_reading_values_and_history_remain_unchanged(
        self, _verify_checksum, _register_handler, parse_frame
    ):
        parse_frame.return_value = self.parsed

        self.handler.process_frame(b"\x68")

        live = LiveReading.objects.get(meter=self.meter)
        history = MeterReading.objects.get(meter=self.meter)
        self.assertEqual(str(live.total_energy), "123.456")
        self.assertEqual(str(live.voltage_a), "230.1")
        self.assertEqual(str(live.current_a), "1.250")
        self.assertEqual(history.total_energy, live.total_energy)
