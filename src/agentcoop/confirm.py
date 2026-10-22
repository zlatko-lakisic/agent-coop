"""Ask the person to allow a change, in a terminal, a window, or a page."""

from __future__ import annotations

import json
import secrets
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from agentcoop.errors import CoopError

DENIED = "The person did not allow this change."
TIMED_OUT = "Nobody allowed the change in time, so nothing changed."
WINDOW_LINE = "Agent Co-op is showing a window on your screen. Click Allow to confirm."
CONFIRM_TIMEOUT_MS = 120000
CONFIRM_TIMEOUT_SECONDS = 120


class TkUnavailable(Exception):
    """tkinter or a display is not available."""


def confirm_person(summary: str, *, expected: str, kind: str, asked_by: str = "") -> None:
    """Confirm a change. Raise when the person does not allow it."""
    if sys.stdin.isatty():
        _confirm_terminal(summary, expected, kind)
        return
    outcome = ask_on_screen(summary, asked_by)
    if outcome in ("allow", True):
        return
    if outcome == "timeout":
        print(TIMED_OUT, flush=True)
        raise CoopError(TIMED_OUT, code=1)
    raise CoopError(DENIED, code=1)


def ask_on_screen(summary: str, asked_by: str) -> str:
    """Show a window, or a browser page when a window is not available.

    Returns allow, cancel, or timeout.
    """
    try:
        return _ask_tk(summary, asked_by)
    except TkUnavailable:
        return _ask_page(summary, asked_by)


def _confirm_terminal(summary: str, expected: str, kind: str) -> None:
    print(summary)
    prompt = "Type the agent name to confirm: " if kind == "agent" else "Type the project name to confirm: "
    if kind == "review":
        prompt = "Type review to confirm: "
    answer = input(prompt)
    if answer.strip() != expected:
        raise CoopError("Cancelled.", code=1)


def _ask_tk(summary: str, asked_by: str) -> str:
    try:
        import tkinter as tk
    except Exception as exc:
        raise TkUnavailable("tkinter is not available") from exc
    try:
        root = tk.Tk()
    except Exception as exc:
        raise TkUnavailable("no display") from exc
    print(WINDOW_LINE, flush=True)
    result = {"value": "cancel"}

    def allow() -> None:
        result["value"] = "allow"
        root.destroy()

    def cancel() -> None:
        result["value"] = "cancel"
        root.destroy()

    def timed_out() -> None:
        result["value"] = "timeout"
        root.destroy()

    root.title("Agent Co-op")
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    who = asked_by.strip() or "an agent"
    tk.Label(root, text=summary, wraplength=420, justify="left").pack(padx=16, pady=(16, 8))
    tk.Label(root, text="Asked by " + who + ".", wraplength=420, justify="left").pack(
        padx=16, pady=(0, 12)
    )
    row = tk.Frame(root)
    row.pack(pady=(0, 16))
    tk.Button(row, text="Allow", command=allow).pack(side="left", padx=8)
    cancel_button = tk.Button(row, text="Cancel", command=cancel)
    cancel_button.pack(side="left", padx=8)
    cancel_button.focus_set()
    root.bind("<Escape>", lambda _event: cancel())
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.after(CONFIRM_TIMEOUT_MS, timed_out)
    root.mainloop()
    return result["value"]


class _ConfirmServer(ThreadingHTTPServer):
    """A page on this computer only. Bind without a reverse DNS lookup."""

    def server_bind(self) -> None:
        if self.allow_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


def _ask_page(summary: str, asked_by: str) -> str:
    token = secrets.token_urlsafe(16)
    result = {"ok": False}
    done = threading.Event()
    page = _confirm_html(summary, asked_by, token).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _allowed(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            return (query.get("t") or [""])[0] == token

        def do_GET(self) -> None:  # noqa: N802
            if not self._allowed():
                self._send(403, b"This page needs the token from Agent Co-op.\n")
                return
            self._send(200, page, "text/html; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            if not self._allowed():
                self._send(403, b"This page needs the token from Agent Co-op.\n")
                return
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b""
            choice = _posted_choice(raw, self.headers.get("Content-Type") or "")
            result["ok"] = choice == "allow"
            done.set()
            self._send(200, b"You can close this page.\n")

        def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = _ConfirmServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:" + str(port) + "/confirm?t=" + token
    print("Agent Co-op needs you to allow this change: " + summary, flush=True)
    print("Open this page to allow or cancel it: " + url, flush=True)
    print("It waits for 2 minutes.", flush=True)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        finished = done.wait(CONFIRM_TIMEOUT_SECONDS)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    if not finished:
        return "timeout"
    return "allow" if result["ok"] else "cancel"


def _posted_choice(raw: bytes, content_type: str) -> str:
    if "application/json" in content_type:
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return ""
        if isinstance(data, dict):
            return str(data.get("choice") or "")
        return ""
    parsed = parse_qs(raw.decode("utf-8", errors="replace"))
    return (parsed.get("choice") or [""])[0]


def _confirm_html(summary: str, asked_by: str, token: str) -> str:
    who = asked_by.strip() or "an agent"
    action = "/answer?t=" + token
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>Agent Co-op</title></head><body>"
        "<p>" + _escape(summary) + "</p>"
        "<p>Asked by " + _escape(who) + ".</p>"
        "<form method=\"POST\" action=\"" + _escape(action) + "\">"
        "<button type=\"submit\" name=\"choice\" value=\"cancel\">Cancel</button> "
        "<button type=\"submit\" name=\"choice\" value=\"allow\">Allow</button>"
        "</form></body></html>"
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
