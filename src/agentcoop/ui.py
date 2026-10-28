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
        if path.startswith("/api/v2/"):
            self._v2(path)
            return
        if path.startswith("/files/"):
            self._shared_file(path)
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        policy = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        if content_type.startswith("text/html"):
            policy = policy.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'", 1)
            policy = policy.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'", 1)
        self.send_header("Content-Security-Policy", policy)
        self.end_headers()
        self.wfile.write(body)

    def _shared_file(self, path: str) -> None:
        """Serve one file from a project's shared folder, and nowhere else."""
        parts = [part for part in path.split("/") if part and part != "."]
        if len(parts) < 3 or parts[0] != "files":
            self._send(404, b"Not found.\n")
            return
        project = parts[1]
        from agentcoop.share import share_root

        root = share_root(self.server.board, project)
        try:
            root_real = root.resolve()
        except OSError:
            self._send(404, b"Not found.\n")
            return
        target = root.joinpath(*parts[2:])
        if target.is_symlink():
            self._send(403, b"That file is not available.\n")
            return
        try:
            real = target.resolve()
        except OSError:
            self._send(404, b"Not found.\n")
            return
        if root_real != real and root_real not in real.parents:
            self._send(403, b"That file is not available.\n")
            return
        if not real.is_file() or real.name.startswith(".") or real.name in ("index.json", "pending.json"):
            self._send(404, b"Not found.\n")
            return
        import mimetypes

        suffix = real.suffix.lower()
        download = suffix in (".svg", ".html", ".htm")
        from agentcoop.share import is_program, load_pending

        held = {str(item.get("name") or "") for item in load_pending(self.server.board, project)}
        if real.name in held or is_program(real):
            self._send(403, b"That file is held.\n")
            return
        media = "application/octet-stream" if download else (mimetypes.guess_type(real.name)[0] or "application/octet-stream")
        try:
            data = real.read_bytes()
        except OSError:
            self._send(404, b"Not found.\n")
            return
        if download:
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", 'attachment; filename="' + real.name.replace('"', "") + '"')
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(200, data, media)

    def _v2(self, path: str) -> None:
        from agentcoop.timeline import (
            agents_view,
            changes_since,
            event_detail,
            histogram,
            projects_view,
            query_events,
            rules_view,
            status_view,
        )

        query = parse_qs(urlparse(self.path).query)
        project = (query.get("project") or [None])[0]
        try:
            if path == "/api/v2/projects":
                payload = {"projects": projects_view(self.server.board)}
            elif path == "/api/v2/events":
                payload = query_events(
                    self.server.board,
                    project=project,
                    start=(query.get("from") or [None])[0],
                    end=(query.get("to") or [None])[0],
                    actors=query.get("actors"),
                    kinds=query.get("kinds"),
                    query=(query.get("q") or [None])[0],
                    severity=(query.get("severity") or [None])[0],
                    cursor=(query.get("cursor") or [None])[0],
                    limit=int((query.get("limit") or ["50"])[0]),
                    include_low=(query.get("low") or ["0"])[0] == "1",
                )
            elif path == "/api/v2/histogram":
                payload = histogram(
                    self.server.board,
                    project=project,
                    start=(query.get("from") or [None])[0],
                    end=(query.get("to") or [None])[0],
                    buckets=int((query.get("buckets") or ["24"])[0]),
                )
            elif path.startswith("/api/v2/event/"):
                detail = event_detail(self.server.board, path.split("/")[-1])
                if detail is None:
                    self._send(404, b"Not found.\n")
                    return
                payload = detail
            elif path == "/api/v2/agents":
                payload = {"agents": agents_view(self.server.board, project or "")}
            elif path == "/api/v2/rules":
                payload = rules_view(self.server.board, project or "")
            elif path == "/api/v2/status":
                payload = status_view(self.server.board)
            elif path == "/api/v2/changes":
                payload = changes_since(self.server.board, (query.get("since") or ["0"])[0])
            else:
                self._send(404, b"Not found.\n")
                return
        except (CoopError, ValueError) as exc:
            self._send(400, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._send(200, json.dumps(payload).encode("utf-8"), "application/json")

    def _state(self) -> dict:
        board = self.server.board
        projects, intact = read_projects_checked(board)
        projects = projects.get("projects") or {}
        agents = load_agents(board).get("agents") or {}
        rows = []
        from agentcoop.context import latest_version, parse_version_name, version_time

        for name, project in projects.items():
            messages = [item.to_dict() for item in log_messages(board, name)]
            latest = latest_version(board, name)
            context = None
            if latest is not None:
                parsed = parse_version_name(latest.name)
                when = version_time(latest.name)
                context = {
                    "version": latest.name,
                    "author": parsed[1] if parsed else "",
                    "time": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "",
                    "body": latest.read_text(encoding="utf-8"),
                }
            rows.append(
                {
                    "name": name,
                    "path": project.get("path", ""),
                    "driver": project.get("driver", ""),
                    "helpers": project.get("helpers") or {},
                    "messages": messages,
                    "context": context,
                    "progress": _progress_row(board, name),
                    "tasks": _task_rows(board, name),
                    "files": _shared_rows(board, name, self.server.token),
                    "grades": _grade_rows(board, name),
                    "reviews": _review_line(board, name, str(project.get("driver") or "")),
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


def _shared_rows(board: Path, project: str, token: str) -> list[dict]:
    """Return shared files the viewer may show. Paths stay inside the shared folder."""
    from urllib.parse import quote

    from agentcoop.share import human_size, load_index, share_root

    root = share_root(board, project)
    rows = []
    for row in load_index(board, project).get("files") or []:
        if not isinstance(row, dict):
            continue
        path = Path(str(row.get("path") or ""))
        if not path.is_file() or path.is_symlink():
            continue
        try:
            relative = path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        url = (
            "/files/"
            + quote(project)
            + "/"
            + "/".join(quote(part) for part in relative.parts)
            + "?t="
            + quote(token)
        )
        rows.append(
            {
                "name": str(row.get("name") or path.name),
                "kind": str(row.get("kind") or "file"),
                "media": str(row.get("media_type") or ""),
                "bytes": int(row.get("bytes") or 0),
                "size": human_size(int(row.get("bytes") or 0)),
                "url": url,
                "description": str(row.get("description") or ""),
            }
        )
    return rows


def _review_line(board: Path, project: str, driver: str) -> str:
    """Return the short review line for the viewer."""
    from agentcoop.taskreview import review_summary

    return review_summary(board, project, driver)


def _grade_rows(board: Path, project: str) -> list[dict]:
    """Return each helper's grade line and the last 20 notes."""
    from agentcoop.grades import viewer_rows

    return viewer_rows(board, project)


def _task_rows(board: Path, project: str) -> list[dict]:
    """Return short progress lines for the viewer, with a percent when one was counted."""
    from agentcoop.progress import helper_tasks, latest_progress, task_lines
    from agentcoop.progressmeta import parse_fields

    rows = []
    lines = task_lines(board, project)
    percents: list[str] = []
    latest = latest_progress(board, project)
    if latest is not None:
        try:
            percents.append(str(parse_fields(latest.read_text(encoding="utf-8")).get("Percent") or ""))
        except Exception:
            percents.append("")
    for task in helper_tasks(board, project):
        percents.append(str(task.get("percent") or ""))
    for index, line in enumerate(lines):
        percent = percents[index] if index < len(percents) else ""
        rows.append({"line": line, "percent": percent})
    return rows


def _progress_row(board: Path, project: str) -> dict | None:
    from agentcoop.progress import latest_progress, parse_progress_name, progress_time

    latest = latest_progress(board, project)
    if latest is None:
        return None
    parsed = parse_progress_name(latest.name)
    when = progress_time(latest.name)
    return {
        "version": latest.name,
        "author": parsed[1] if parsed else "",
        "time": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "",
        "body": latest.read_text(encoding="utf-8"),
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
