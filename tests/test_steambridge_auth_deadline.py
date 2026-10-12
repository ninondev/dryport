# SPDX-License-Identifier: GPL-3.0-only
# Copyright (c) 2026 Ninon
"""Unauthenticated clients have a total deadline, including trickled bytes."""
import secrets
import socket
import struct
import threading
import time

import test_steambridge_token as fixtures


class AuthDeadlineTests(fixtures.HelperTokenTest):
    BOUND = 3

    def valid_hello(self, port, token, calls):
        started = time.monotonic()
        good = fixtures.TokenClient(port)
        try:
            good.sock.settimeout(self.BOUND)
            good.send(token.encode() + fixtures.frame(fixtures.F_HELLO))
            self.assertEqual(good.read_reply(), b"\x01")
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, self.BOUND)
            print(f"valid HELLO: {elapsed}s, bound {self.BOUND}s")
            self.assertEqual(self.mock_calls(calls), "")
        finally:
            good.close()

    def test_three_silent_clients_add_less_than_three_seconds(self):
        token = secrets.token_hex(32)
        process, port, calls = self.start_helper(token=token)
        bad = [fixtures.TokenClient(port) for _ in range(3)]
        try:
            self.valid_hello(port, token, calls)
            self.assertIsNone(process.poll())
        finally:
            for client in bad:
                client.close()

    def test_stream_of_silent_clients_adds_less_than_three_seconds(self):
        token = secrets.token_hex(32)
        process, port, calls = self.start_helper(token=token)
        stop = threading.Event()
        bad = []
        def stream():
            while not stop.is_set():
                try:
                    bad.append(fixtures.TokenClient(port))
                except OSError:
                    return
                stop.wait(0.2)
        sender = threading.Thread(target=stream)
        sender.start()
        try:
            time.sleep(0.5)
            self.valid_hello(port, token, calls)
        finally:
            stop.set(); sender.join(timeout=12)
            self.assertFalse(sender.is_alive())
            for client in bad:
                client.close()

    def test_pending_authentication_queue_is_capped_at_four(self):
        token = secrets.token_hex(32)
        process, port, calls = self.start_helper(token=token)
        sockets = []
        try:
            for _ in range(20):
                sockets.append(socket.create_connection(("127.0.0.1", port), timeout=1))
            self.valid_hello(port, token, calls)
            closed = 0
            for client in sockets:
                client.settimeout(0.1)
                try:
                    closed += client.recv(1) == b""
                except ConnectionResetError:
                    closed += 1
                except socket.timeout:
                    # socket.timeout is an alias of TimeoutError only on
                    # Python 3.10+; on the supported 3.9 it is a plain OSError.
                    pass
            self.assertGreaterEqual(closed, 16)
            self.assertIsNone(process.poll())
        finally:
            for client in sockets:
                client.close()

    def test_authenticated_silent_client_does_not_delay_valid_client(self):
        token = secrets.token_hex(32)
        process, port, calls = self.start_helper(token=token)
        bad = fixtures.TokenClient(port)
        try:
            bad.send(token.encode())
            time.sleep(0.1)
            self.valid_hello(port, token, calls)
        finally:
            bad.close()

    def test_authenticated_reads_have_a_total_deadline(self):
        for phase in ("first-header", "next-header", "payload", "trickled-payload"):
            with self.subTest(phase=phase):
                token = secrets.token_hex(32)
                process, port, calls = self.start_helper(token=token)
                bad = fixtures.TokenClient(port)
                stop = threading.Event()
                sender = None
                try:
                    bad.send(token.encode())
                    if phase == "next-header":
                        bad.send(fixtures.frame(fixtures.F_HELLO))
                        self.assertEqual(bad.read_reply(), b"\x01")
                        bad.send(b"\x01")
                    elif "payload" in phase:
                        bad.send(struct.pack("<HI", fixtures.F_INIT, 1024))
                        if phase == "trickled-payload":
                            def trickle():
                                while not stop.wait(0.4):
                                    try:
                                        bad.send(b"\x00")
                                    except OSError:
                                        return
                            sender = threading.Thread(target=trickle)
                            sender.start()
                    bad.sock.settimeout(self.BOUND)
                    started = time.monotonic()
                    bad.expect_closed()
                    self.assertLess(time.monotonic() - started, self.BOUND)
                    self.assertEqual(self.mock_calls(calls), "")
                finally:
                    stop.set()
                    if sender:
                        sender.join(timeout=2)
                    bad.close()

    def check_blocker(self, kind):
        token = secrets.token_hex(32)
        process, port, calls = self.start_helper(token=token)
        bad = fixtures.TokenClient(port)
        stopped = threading.Event()
        sender = None
        good = None
        try:
            if kind == "partial":
                bad.send(token[:5].encode())
            elif kind == "trickle":
                def trickle():
                    for byte in token[:8].encode():
                        if stopped.is_set():
                            break
                        try:
                            bad.send(bytes([byte]))
                        except OSError:
                            break
                        stopped.wait(0.4)
                sender = threading.Thread(target=trickle)
                sender.start()
            good = fixtures.TokenClient(port)
            good.sock.settimeout(4)
            started = time.monotonic()
            good.send(token.encode() + fixtures.frame(fixtures.F_HELLO))
            self.assertEqual(good.read_reply(), b"\x01")
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(self.mock_calls(calls), "", "Unauthenticated bytes must not enter Steam tables")
            self.assertIsNone(process.poll())
        finally:
            stopped.set()
            if sender:
                sender.join(timeout=2)
            bad.close()
            if good:
                good.close()

    def test_silent_client_does_not_block_a_valid_client(self):
        self.check_blocker("silent")

    def test_partial_token_does_not_block_a_valid_client(self):
        self.check_blocker("partial")

    def test_trickled_bytes_do_not_reset_authentication_deadline(self):
        self.check_blocker("trickle")
