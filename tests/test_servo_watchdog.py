from pathlib import Path
import os
import socket
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / "pi"))
from servos import SystemdWatchdog


class ServoWatchdogTests(unittest.TestCase):
    def test_kernel_driver_cannot_start_without_independent_watchdog(self):
        for environment in ({}, {"NOTIFY_SOCKET": "/unused"},
                            {"NOTIFY_SOCKET": "/unused", "WATCHDOG_USEC": "5000000"},
                            {"NOTIFY_SOCKET": "/unused", "WATCHDOG_USEC": "bad"},
                            {"NOTIFY_SOCKET": "/unused", "WATCHDOG_USEC": "1000000", "WATCHDOG_PID": "0"}):
            with self.subTest(environment=environment), self.assertRaises(OSError):
                SystemdWatchdog(required=True, environment=environment)

    def test_real_notify_socket_gets_readiness_and_only_loop_driven_heartbeats(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            address = str(Path(directory) / "notify")
            now = [0.0]
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
                receiver.bind(address)
                receiver.settimeout(.02)
                watchdog = SystemdWatchdog(required=True, environment={
                    "NOTIFY_SOCKET": address, "WATCHDOG_USEC": "1000000",
                    "WATCHDOG_PID": str(os.getpid())}, clock=lambda: now[0])
                watchdog.ready()
                self.assertEqual(receiver.recv(512), b"READY=1")
                self.assertEqual(receiver.recv(512), b"WATCHDOG=1")
                now[0] = .2
                watchdog.tick()
                with self.assertRaises(socket.timeout): receiver.recv(512)
                now[0] = .4
                # Time alone cannot renew a stalled command loop.
                with self.assertRaises(socket.timeout): receiver.recv(512)
                watchdog.tick()
                self.assertEqual(receiver.recv(512), b"WATCHDOG=1")

    def test_missing_notify_receiver_is_fatal_instead_of_fake_readiness(self):
        watchdog = SystemdWatchdog(required=True, environment={
            "NOTIFY_SOCKET": "/nonexistent/denden-notify", "WATCHDOG_USEC": "1000000"})
        with self.assertRaises(OSError): watchdog.ready()


if __name__ == "__main__":
    unittest.main()
