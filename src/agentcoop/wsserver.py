"""RFC 6455 WebSocket framing on top of a connected socket.

No third-party package. Used by the local board viewer.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading
from typing import Callable

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA
MAX_MESSAGE_BYTES = 1_048_576
CLOSE_NORMAL = 1000
CLOSE_PROTOCOL = 1002
CLOSE_TOO_BIG = 1009
CLOSE_POLICY = 1008


class WebSocketError(Exception):
    """A framing or handshake problem."""

    def __init__(self, message: str, code: int = CLOSE_PROTOCOL) -> None:
        super().__init__(message)
        self.code = code


def accept_key(sec_websocket_key: str) -> str:
    """Return the Sec-WebSocket-Accept value for a client key."""
    digest = hashlib.sha1((sec_websocket_key.strip() + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_response(sec_websocket_key: str, *, extra_headers: list[tuple[str, str]] | None = None) -> bytes:
    """Build the HTTP 101 response bytes for a successful upgrade."""
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: " + accept_key(sec_websocket_key),
    ]
    for name, value in extra_headers or []:
        lines.append(name + ": " + value)
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("ascii")


def parse_upgrade_headers(headers) -> dict[str, str]:
    """Return lower-case header names for the upgrade check."""
    if hasattr(headers, "items"):
        return {str(key).lower(): str(value) for key, value in headers.items()}
    return {str(key).lower(): str(value) for key, value in headers}


def is_websocket_upgrade(headers) -> bool:
    """True when the request asks to upgrade to WebSocket."""
    values = parse_upgrade_headers(headers)
    connection = values.get("connection", "").lower()
    upgrade = values.get("upgrade", "").lower()
    return "upgrade" in connection and upgrade == "websocket" and bool(values.get("sec-websocket-key"))


class WebSocketConnection:
    """One WebSocket over a stream socket. Server side expects masked client frames."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        server: bool = True,
    ) -> None:
        self.sock = sock
        self.max_message_bytes = max_message_bytes
        self.server = server
        self.closed = False
        self._lock = threading.Lock()
        self._recv_buf = bytearray()

    def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        """Send a close frame and shut the socket down."""
        if self.closed:
            return
        payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
        try:
            self._send_frame(OPCODE_CLOSE, payload)
        except OSError:
            pass
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass

    def send_text(self, text: str) -> None:
        """Send one text message."""
        data = text.encode("utf-8")
        if len(data) > self.max_message_bytes:
            raise WebSocketError("Message is too large.", CLOSE_TOO_BIG)
        self._send_frame(OPCODE_TEXT, data)

    def send_ping(self, payload: bytes = b"") -> None:
        """Send a ping control frame."""
        self._send_frame(OPCODE_PING, payload[:125])

    def send_pong(self, payload: bytes = b"") -> None:
        """Send a pong control frame."""
        self._send_frame(OPCODE_PONG, payload[:125])

    def recv_message(self, timeout: float | None = None) -> tuple[int, bytes]:
        """Read the next complete data or close message.

        Ping frames are answered with pong and skipped.
        Returns (opcode, payload). Close returns OPCODE_CLOSE.
        """
        if timeout is not None:
            self.sock.settimeout(timeout)
        fragments: list[bytes] = []
        message_opcode: int | None = None
        while True:
            opcode, fin, payload = self._recv_frame()
            if opcode in (OPCODE_PING, OPCODE_PONG, OPCODE_CLOSE):
                if opcode == OPCODE_PING:
                    self.send_pong(payload)
                    continue
                if opcode == OPCODE_PONG:
                    continue
                self.closed = True
                return OPCODE_CLOSE, payload
            if opcode in (OPCODE_TEXT, OPCODE_BINARY):
                if message_opcode is not None:
                    raise WebSocketError("A new data frame started before the previous finished.")
                message_opcode = opcode
                fragments = [payload]
            elif opcode == OPCODE_CONTINUATION:
                if message_opcode is None:
                    raise WebSocketError("Continuation without a starting frame.")
                fragments.append(payload)
            else:
                raise WebSocketError("Unknown opcode " + str(opcode))
            total = sum(len(part) for part in fragments)
            if total > self.max_message_bytes:
                raise WebSocketError("Message is too large.", CLOSE_TOO_BIG)
            if fin:
                return message_opcode, b"".join(fragments)

    def iter_text(self, *, on_ping: Callable[[], None] | None = None) -> str:
        """Yield text payloads until the connection closes."""
        while not self.closed:
            opcode, payload = self.recv_message()
            if opcode == OPCODE_CLOSE:
                return
            if opcode == OPCODE_TEXT:
                if on_ping:
                    on_ping()
                yield payload.decode("utf-8")
            # binary frames are ignored for this viewer protocol

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise WebSocketError("The socket is closed.")
        length = len(payload)
        header = bytearray()
        header.append(0x80 | (opcode & 0x0F))
        mask_bit = 0x80 if not self.server else 0x00
        if length < 126:
            header.append(mask_bit | length)
        elif length < 65536:
            header.append(mask_bit | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(mask_bit | 127)
            header.extend(struct.pack("!Q", length))
        data = payload
        if not self.server:
            mask = os.urandom(4)
            header.extend(mask)
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self._lock:
            self.sock.sendall(header + data)

    def _recv_frame(self) -> tuple[int, bool, bytes]:
        first = self._read_exact(2)
        fin = (first[0] & 0x80) != 0
        opcode = first[0] & 0x0F
        masked = (first[1] & 0x80) != 0
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if length > self.max_message_bytes and opcode in (OPCODE_TEXT, OPCODE_BINARY, OPCODE_CONTINUATION):
            raise WebSocketError("Message is too large.", CLOSE_TOO_BIG)
        if self.server and not masked and opcode != OPCODE_CLOSE:
            # Clients must mask. Allow empty close during tests of raw sockets carefully.
            if opcode in (OPCODE_TEXT, OPCODE_BINARY, OPCODE_CONTINUATION, OPCODE_PING, OPCODE_PONG):
                raise WebSocketError("Client frames must be masked.")
        mask = self._read_exact(4) if masked else b""
        raw = self._read_exact(length) if length else b""
        if masked:
            raw = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
        return opcode, fin, raw

    def _read_exact(self, size: int) -> bytes:
        while len(self._recv_buf) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WebSocketError("The socket closed while reading.", CLOSE_PROTOCOL)
            self._recv_buf.extend(chunk)
        data = bytes(self._recv_buf[:size])
        del self._recv_buf[:size]
        return data


def connect_client(host: str, port: int, path: str = "/", *, origin: str | None = None, headers: dict[str, str] | None = None) -> WebSocketConnection:
    """Open a client WebSocket. Used by tests."""
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    sock = socket.create_connection((host, port), timeout=10)
    lines = [
        "GET " + path + " HTTP/1.1",
        "Host: " + host + ":" + str(port),
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: " + key,
        "Sec-WebSocket-Version: 13",
    ]
    if origin:
        lines.append("Origin: " + origin)
    for name, value in (headers or {}).items():
        lines.append(name + ": " + value)
    lines.append("")
    lines.append("")
    sock.sendall("\r\n".join(lines).encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            sock.close()
            raise WebSocketError("No handshake response.")
        response += chunk
    head, _, leftover = response.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0]
    if b"101" not in status:
        sock.close()
        raise WebSocketError("Upgrade refused: " + status.decode("ascii", "replace"))
    expected = accept_key(key)
    if expected.encode("ascii") not in head:
        sock.close()
        raise WebSocketError("Bad Sec-WebSocket-Accept.")
    conn = WebSocketConnection(sock, server=False)
    if leftover:
        conn._recv_buf.extend(leftover)
    return conn
