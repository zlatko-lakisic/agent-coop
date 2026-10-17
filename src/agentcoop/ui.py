"""A local viewer for the human. Agents do not talk to it."""

from __future__ import annotations

import json
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from agentcoop.core import log_messages
from agentcoop.doctor import format_doctor, run_doctor
from agentcoop.errors import CoopError
from agentcoop.install import read_template
from agentcoop.paths import board_root, home_dir
from agentcoop.projects import INTEGRITY_WARNING, load_agents, read_projects_checked, require_board
from agentcoop.tools import tool_can_drive
from agentcoop.wizard import add_project_flow, change_driver, helper_add, helper_remove

_SKIP_DIRS = {"Library", "Applications", "node_modules", "Pictures", "Movies", "Music"}


class ViewerServer(ThreadingHTTPServer):
    """HTTP server bound to this computer only."""

    def __init__(self, board: Path, token: str, host: str = "127.0.0.1", port: int = 0) -> None:
        if host != "127.0.0.1":
            raise CoopError("The viewer only runs on 127.0.0.1.")
        self.board = board
        self.token = token
        self.idle_at = time.time()
        super().__init__((host, port), ViewerHandler)

    def server_bind(self) -> None:
        """Bind without a reverse DNS lookup. That lookup can hang on some computers."""
        if self.allow_reuse_address:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = "127.0.0.1"
        self.server_port = self.server_address[1]


class ViewerHandler(BaseHTTPRequestHandler):
    """Serve the one page and the JSON actions behind it."""

    server: ViewerServer

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self.server.idle_at = time.time()
        if not self._allowed():
            self._send(403, b"This page needs the token from the agentcoop ui command.\n")
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = read_template("ui.html").encode("utf-8")
            self._send(200, page, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            self._send(200, json.dumps(self._state()).encode("utf-8"), "application/json")
            return
        if path == "/api/suggestions":
            self._send(200, json.dumps(suggest_repos(home_dir())).encode("utf-8"), "application/json")
            return
        if path == "/api/doctor":
            query = parse_qs(urlparse(self.path).query)
            project = (query.get("project") or [None])[0]
            text = format_doctor(run_doctor(self.server.board, project=project))
            self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._send(404, b"Not found.\n")

    def do_POST(self) -> None:  # noqa: N802
        self.server.idle_at = time.time()
        if not self._allowed():
            self._send(403, b"This page needs the token from the agentcoop ui command.\n")
            return
        path = urlparse(self.path).path
        if path == "/api/shutdown":
            self._send(200, b"Closing.\n")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        try:
            payload = self._json()
            if path == "/api/driver":
                text = change_driver(payload["project"], payload["agent"], yes=True)
            elif path == "/api/helper/add":
                text = helper_add(payload["project"], payload["agent"], payload.get("note") or "helps the driver")
            elif path == "/api/helper/remove":
                text = helper_remove(payload["project"], payload["agent"])
            elif path == "/api/project":
                text = add_project_flow(
                    path=Path(payload["path"]),
                    name=payload.get("name"),
                    driver=payload.get("driver"),
                    helpers=payload.get("helpers") or {},
                    yes=True,
                    gitignore=True,
                )
            else:
                self._send(404, b"Not found.\n")
                return
        except CoopError as exc:
            body = str(exc)
            if exc.code == 5:
                body = "BANNER: " + body
            self._send(400, body.encode("utf-8"), "text/plain; charset=utf-8")
            return
        except (KeyError, OSError) as exc:
            self._send(400, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")

    def _allowed(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        supplied = (query.get("t") or [""])[0]
        header = self.headers.get("X-Agentcoop-Token", "")
        return supplied == self.server.token or header == self.server.token

    def _json(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise CoopError("The request was not a JSON object.")
        return data

    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _state(self) -> dict:
        board = self.server.board
        projects, intact = read_projects_checked(board)
        projects = projects.get("projects") or {}
        agents = load_agents(board).get("agents") or {}
        rows = []
        for name, project in projects.items():
            messages = [item.to_dict() for item in log_messages(board, name)]
            rows.append(
                {
                    "name": name,
                    "path": project.get("path", ""),
                    "driver": project.get("driver", ""),
                    "helpers": project.get("helpers") or {},
                    "messages": messages,
                }
            )
        agent_rows = []
        for name, record in agents.items():
            tool_key = record.get("tool", "")
            try:
                from agentcoop.tools import get_tool

                note = get_tool(tool_key).helper_note
            except KeyError:
                note = "helps the driver"
            agent_rows.append(
                {
                    "name": name,
                    "display": record.get("display", name),
                    "can_drive": tool_can_drive(tool_key),
                    "note": note,
                }
            )
        banner = ""
        if not intact:
            banner = INTEGRITY_WARNING
        return {
            "projects": rows,
            "agents": agent_rows,
            "note": "This page is for you. Agents do not connect to it.",
            "banner": banner,
        }


def suggest_repos(home: Path, limit: int = 20) -> list[str]:
    """List git repos one or two levels under the home folder."""
    found: list[str] = []
    try:
        children = list(home.iterdir())
    except OSError:
        return []
    for child in children:
        if child.name.startswith(".") or child.name in _SKIP_DIRS or not child.is_dir():
            continue
        if (child / ".git").exists():
            found.append(str(child))
        try:
            for grand in child.iterdir():
                if len(found) >= limit:
                    return found
                if grand.name.startswith(".") or not grand.is_dir():
                    continue
                if (grand / ".git").exists():
                    found.append(str(grand))
        except OSError:
            continue
    return found[:limit]


def make_server(board: Path, token: str, host: str = "127.0.0.1", port: int = 0) -> ViewerServer:
    """Create the viewer server. Any host other than 127.0.0.1 is refused."""
    return ViewerServer(board, token, host=host, port=port)


def serve(*, open_browser: bool = True, idle_seconds: int = 1800) -> int:
    """Run the viewer until it is closed, stopped, or left idle."""
    board = board_root()
    require_board(board)
    token = secrets.token_urlsafe(24)
    server = make_server(board, token)
    port = server.server_address[1]
    url = "http://127.0.0.1:" + str(port) + "/?t=" + token

    def idle() -> None:
        while True:
            time.sleep(5)
            if time.time() - server.idle_at > idle_seconds:
                server.shutdown()
                return

    threading.Thread(target=idle, daemon=True).start()
    print(url, flush=True)
    print("This page is for you. Agents do not connect to it.", flush=True)
    print("Press Ctrl+C to stop. It also stops after 30 minutes with no requests.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
