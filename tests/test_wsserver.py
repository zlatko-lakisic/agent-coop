"""RFC 6455 framing used by the viewer."""

from __future__ import annotations

import socket
import struct
import threading
import unittest

from agentcoop.wsserver import (
    CLOSE_TOO_BIG,
    MAX_MESSAGE_BYTES,
    OPCODE_CLOSE,
    OPCODE_PING,
    OPCODE_PONG,
    OPCODE_TEXT,
    WebSocketConnection,
    WebSocketError,
    accept_key,
    connect_client,
    handshake_response,
)


def _pair() -> tuple[socket.socket, socket.socket]:
    left, right = socket.socketpair()
    return left, right


class HandshakeTests(unittest.TestCase):
    def test_accept_key_known_vector(self) -> None:
        # RFC 6455 example key.
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_handshake_response_bytes(self) -> None:
        raw = handshake_response("dGhlIHNhbXBsZSBub25jZQ==", extra_headers=[("Set-Cookie", "a=b")])
        text = raw.decode("ascii")
        self.assertIn("101 Switching Protocols", text)
        self.assertIn("Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", text)
        self.assertIn("Set-Cookie: a=b", text)


class FrameTests(unittest.TestCase):
    def test_text_masked_and_split(self) -> None:
        server_sock, client_sock = _pair()
        server = WebSocketConnection(server_sock, server=True)
        client = WebSocketConnection(client_sock, server=False)
        client.send_text("hello")
        opcode, payload = server.recv_message(timeout=2)
        self.assertEqual(opcode, OPCODE_TEXT)
        self.assertEqual(payload, b"hello")

        # Split across two frames: first continuation start, then finish.
        part1 = "abcdefghij"
        part2 = "klmnop"
        mask1 = b"\x01\x02\x03\x04"
        data1 = bytes(b ^ mask1[i % 4] for i, b in enumerate(part1.encode()))
        header1 = bytes([0x01, 0x80 | len(part1)]) + mask1 + data1
        mask2 = b"\x05\x06\x07\x08"
        data2 = bytes(b ^ mask2[i % 4] for i, b in enumerate(part2.encode()))
        header2 = bytes([0x80, 0x80 | len(part2)]) + mask2 + data2
        client_sock.sendall(header1 + header2)
        opcode, payload = server.recv_message(timeout=2)
        self.assertEqual(opcode, OPCODE_TEXT)
        self.assertEqual(payload.decode(), part1 + part2)
        server.close()
        client.close()

    def test_ping_pong_and_close(self) -> None:
        server_sock, client_sock = _pair()
        server = WebSocketConnection(server_sock, server=True)
        client = WebSocketConnection(client_sock, server=False)

        def client_side() -> None:
            client.send_ping(b"xy")
            first = client_sock.recv(64)
            self.assertTrue(first)
            self.assertEqual(first[0] & 0x0F, OPCODE_PONG)
            client.close(1000, "bye")

        thread = threading.Thread(target=client_side)
        thread.start()
        opcode, _payload = server.recv_message(timeout=2)
        self.assertEqual(opcode, OPCODE_CLOSE)
        thread.join(timeout=2)
        server.close()

    def test_oversized_rejected(self) -> None:
        server_sock, client_sock = _pair()
        server = WebSocketConnection(server_sock, server=True, max_message_bytes=64)
        client = WebSocketConnection(client_sock, server=False, max_message_bytes=64)
        with self.assertRaises(WebSocketError) as raised:
            client.send_text("x" * 128)
        self.assertEqual(raised.exception.code, CLOSE_TOO_BIG)
        # Frame that claims a huge length should fail on the server.
        mask = b"\x00\x00\x00\x00"
        header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", 200) + mask + (b"\x00" * 200)
        client_sock.sendall(header)
        with self.assertRaises(WebSocketError):
            server.recv_message(timeout=2)
        server.close()
        client.close()

    def test_unmasked_client_frame_refused(self) -> None:
        server_sock, client_sock = _pair()
        server = WebSocketConnection(server_sock, server=True)
        # Unmasked text frame from "client".
        payload = b"nope"
        client_sock.sendall(bytes([0x81, len(payload)]) + payload)
        with self.assertRaises(WebSocketError):
            server.recv_message(timeout=2)
        server.close()
        client_sock.close()


if __name__ == "__main__":
    unittest.main()
