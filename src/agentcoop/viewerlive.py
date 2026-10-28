"""WebSocket protocol for the board viewer: hello, ops, subscribe, and live pushes."""

from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any

from agentcoop.errors import CoopError
from agentcoop.wsserver import (
    CLOSE_NORMAL,
    CLOSE_POLICY,
    OPCODE_CLOSE,
    OPCODE_TEXT,
    WebSocketConnection,
    WebSocketError,
)

HELLO_DEADLINE_SECONDS = 5
PING_EVERY_SECONDS = 20
IDLE_CLOSE_SECONDS = 60
MAX_CONNECTIONS = 8
MAX_REPLAY = 5000
MAX_REPLAY_AGE_SECONDS = 600


class LiveHub:
    """One hub per viewer server: connections, file sessions, and the board watcher."""

    def __init__(self, board: Path, token: str) -> None:
        self.board = board
        self.token = token
        self.lock = threading.Lock()
        self.connections: set[LiveSession] = set()
        self.file_sessions: dict[str, dict[str, Any]] = {}
        self.push_log: list[dict[str, Any]] = []
        self.seq = 0
        self._watcher_stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self._last_signature: tuple | None = None
        self._last_event_ids: dict[str, str] = {}
        self._last_status: str = ""
        self._last_agents: dict[str, str] = {}
        self._last_rules: dict[str, str] = {}
        self._last_histogram: dict[str, str] = {}

    def start(self) -> None:
        """Start the once-a-second board watcher."""
        if self._watcher is not None:
            return
        self._watcher_stop.clear()
        self._watcher = threading.Thread(target=self._watch_loop, name="viewer-live", daemon=True)
        self._watcher.start()

    def stop(self) -> None:
        """Stop the watcher and close every socket."""
        self._watcher_stop.set()
        with self.lock:
            sessions = list(self.connections)
        for session in sessions:
            session.close(CLOSE_NORMAL, "server stop")

    def can_accept(self) -> bool:
        with self.lock:
            return len(self.connections) < MAX_CONNECTIONS

    def register(self, session: "LiveSession") -> None:
        with self.lock:
            self.connections.add(session)

    def unregister(self, session: "LiveSession") -> None:
        with self.lock:
            self.connections.discard(session)

    def new_file_session(self) -> str:
        """Create an inactive file cookie value for a new WebSocket."""
        value = secrets.token_urlsafe(24)
        with self.lock:
            self.file_sessions[value] = {"active": False, "created": time.time()}
        return value

    def activate_file_session(self, value: str) -> None:
        with self.lock:
            row = self.file_sessions.get(value)
            if row is not None:
                row["active"] = True
                row["activated"] = time.time()

    def file_session_ok(self, value: str) -> bool:
        with self.lock:
            row = self.file_sessions.get(value or "")
            return bool(row and row.get("active"))

    def touch_activity(self) -> None:
        """Hook for the server idle timer. Sessions call this on traffic."""
        return

    def push(self, message: dict[str, Any]) -> int:
        """Append a sequenced push and deliver it to matching subscribers."""
        with self.lock:
            self.seq += 1
            message = dict(message)
            message["seq"] = self.seq
            message["ts"] = time.time()
            self.push_log.append(message)
            cutoff = time.time() - MAX_REPLAY_AGE_SECONDS
            while len(self.push_log) > MAX_REPLAY or (self.push_log and self.push_log[0].get("ts", 0) < cutoff):
                if len(self.push_log) <= 1:
                    break
                if len(self.push_log) > MAX_REPLAY:
                    self.push_log.pop(0)
                    continue
                if self.push_log[0].get("ts", 0) < cutoff:
                    self.push_log.pop(0)
                    continue
                break
            sessions = list(self.connections)
            seq = self.seq
        for session in sessions:
            if session.wants(message):
                session.send_push(message)
        return seq

    def replay_since(self, last_seq: int) -> tuple[list[dict[str, Any]] | None, str | None]:
        """Return missed pushes, or (None, reason) when the client must reload."""
        with self.lock:
            if not self.push_log:
                if last_seq <= self.seq:
                    return [], None
                return None, "Too much was missed. Reload the view."
            oldest = int(self.push_log[0].get("seq") or 0)
            newest = int(self.push_log[-1].get("seq") or 0)
            if last_seq < oldest - 1 or (self.seq - last_seq) > MAX_REPLAY:
                return None, "Too much was missed. Reload the view."
            if last_seq > newest:
                return [], None
            missed = [row for row in self.push_log if int(row.get("seq") or 0) > last_seq]
            if missed and time.time() - float(missed[0].get("ts") or time.time()) > MAX_REPLAY_AGE_SECONDS:
                return None, "The gap is older than ten minutes. Reload the view."
            return [dict(row) for row in missed], None

    def _watch_loop(self) -> None:
        from agentcoop.timeline import (
            _signature,
            agents_view,
            clear_cache,
            events_for,
            histogram,
            rules_view,
            status_view,
        )

        while not self._watcher_stop.wait(1.0):
            try:
                signature = _signature(self.board, None)
            except Exception:
                continue
            if signature == self._last_signature:
                continue
            first_pass = self._last_signature is None
            self._last_signature = signature
            try:
                clear_cache()
                events = events_for(self.board, None)
            except Exception:
                continue
            by_id = {str(event.get("id")): event for event in events}
            for event_id, event in by_id.items():
                fingerprint = _event_fingerprint(event)
                previous = self._last_event_ids.get(event_id)
                if not first_pass:
                    if previous is None:
                        self.push({"type": "event.added", "event": _public_event(event)})
                    elif previous != fingerprint:
                        self.push({"type": "event.updated", "event": _public_event(event)})
                self._last_event_ids[event_id] = fingerprint
            gone = [eid for eid in list(self._last_event_ids) if eid not in by_id]
            for eid in gone:
                del self._last_event_ids[eid]

            try:
                status = status_view(self.board)
                status_key = json.dumps(status, sort_keys=True)
                if status_key != self._last_status:
                    self._last_status = status_key
                    if not first_pass:
                        self.push({"type": "status", "data": status})
            except Exception:
                pass

            projects = _project_names(self.board)
            for name in projects:
                try:
                    agents = agents_view(self.board, name)
                    key = json.dumps(agents, sort_keys=True)
                    if self._last_agents.get(name) != key:
                        self._last_agents[name] = key
                        if not first_pass:
                            self.push({"type": "agents", "project": name, "data": {"agents": agents}})
                except Exception:
                    pass
                try:
                    rules = rules_view(self.board, name)
                    key = json.dumps(rules, sort_keys=True)
                    if self._last_rules.get(name) != key:
                        self._last_rules[name] = key
                        if not first_pass:
                            self.push({"type": "rules", "project": name, "data": rules})
                except Exception:
                    pass
                try:
                    hist = histogram(self.board, project=name, buckets=24)
                    key = json.dumps(hist, sort_keys=True)
                    if self._last_histogram.get(name) != key:
                        previous = self._last_histogram.get(name)
                        self._last_histogram[name] = key
                        if previous is not None and not first_pass:
                            self.push({"type": "histogram.delta", "project": name, "data": hist})
                except Exception:
                    pass


class LiveSession:
    """One authenticated or pending WebSocket session."""

    def __init__(
        self,
        hub: LiveHub,
        conn: WebSocketConnection,
        *,
        file_session: str,
        origin: str,
        on_activity,
        readonly: bool = False,
        auth_tokens: set[str] | None = None,
        phone_tokens: set[str] | None = None,
    ) -> None:
        self.hub = hub
        self.conn = conn
        self.file_session = file_session
        self.origin = origin
        self.on_activity = on_activity
        self.readonly = readonly
        self.auth_tokens = auth_tokens or {hub.token}
        self.phone_tokens = phone_tokens or set()
        self.authed = False
        self.closed = False
        self.subscription: dict[str, Any] | None = None
        self.last_seen = time.time()
        self._send_lock = threading.Lock()

    def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        self.hub.unregister(self)
        try:
            self.conn.close(code, reason)
        except Exception:
            pass

    def wants(self, message: dict[str, Any]) -> bool:
        if not self.authed or self.subscription is None:
            return False
        sub = self.subscription
        project = sub.get("project")
        msg_project = message.get("project") or (message.get("event") or {}).get("project")
        if project and msg_project and msg_project != project:
            if message.get("type") not in ("status",):
                return False
        return True

    def send_push(self, message: dict[str, Any]) -> None:
        try:
            self._send_json(message)
        except Exception:
            self.close(CLOSE_POLICY, "send failed")

    def run(self) -> None:
        """Block until the socket closes. Call from a dedicated thread."""
        self.hub.register(self)
        try:
            if not self._await_hello():
                return
            self._pump()
        except (WebSocketError, OSError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
            self.close(CLOSE_POLICY, "protocol error")
        finally:
            self.close()

    def _await_hello(self) -> bool:
        deadline = time.time() + HELLO_DEADLINE_SECONDS
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            try:
                opcode, payload = self.conn.recv_message(timeout=remaining)
            except (TimeoutError, socket.timeout):
                continue
            except (WebSocketError, OSError):
                self.close(CLOSE_POLICY, "hello closed")
                return False
            self._touch()
            if opcode == OPCODE_CLOSE:
                self.close()
                return False
            if opcode != OPCODE_TEXT:
                continue
            try:
                message = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.close(CLOSE_POLICY, "bad hello")
                return False
            if not isinstance(message, dict) or message.get("type") != "hello":
                self.close(CLOSE_POLICY, "hello required")
                return False
            token = str(message.get("token") or "")
            if token not in self.auth_tokens:
                self.close(CLOSE_POLICY, "bad token")
                return False
            if token in self.phone_tokens:
                self.readonly = True
            self.authed = True
            self.hub.activate_file_session(self.file_session)
            self._send_json(
                {
                    "type": "hello.ok",
                    "file_session": self.file_session,
                    "readonly": self.readonly,
                }
            )
            return True
        self.close(CLOSE_POLICY, "hello timeout")
        return False

    def _pump(self) -> None:
        next_ping = time.time() + PING_EVERY_SECONDS
        while not self.closed:
            timeout = max(0.2, min(2.0, next_ping - time.time()))
            try:
                opcode, payload = self.conn.recv_message(timeout=timeout)
            except (TimeoutError, socket.timeout):
                if time.time() - self.last_seen > IDLE_CLOSE_SECONDS:
                    self.close(CLOSE_POLICY, "idle")
                    return
                if time.time() >= next_ping:
                    try:
                        self.conn.send_ping(b"ping")
                    except Exception:
                        self.close(CLOSE_POLICY, "ping failed")
                        return
                    next_ping = time.time() + PING_EVERY_SECONDS
                continue
            self._touch()
            if opcode == OPCODE_CLOSE:
                self.close()
                return
            if opcode != OPCODE_TEXT:
                continue
            try:
                message = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._reply_error(None, "That message was not valid JSON.")
                continue
            if not isinstance(message, dict):
                self._reply_error(None, "That message was not a JSON object.")
                continue
            self._handle(message)
            if time.time() >= next_ping:
                try:
                    self.conn.send_ping(b"ping")
                except Exception:
                    self.close(CLOSE_POLICY, "ping failed")
                    return
                next_ping = time.time() + PING_EVERY_SECONDS

    def _handle(self, message: dict[str, Any]) -> None:
        req_id = message.get("id")
        op = message.get("op")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if not op:
            self._reply_error(req_id, "Missing op.")
            return
        try:
            data = self._dispatch(str(op), params)
        except CoopError as exc:
            self._reply_error(req_id, str(exc))
            return
        except (KeyError, TypeError, ValueError, OSError) as exc:
            self._reply_error(req_id, str(exc) or "The request failed.")
            return
        self._send_json({"id": req_id, "ok": True, "data": data})

    def _dispatch(self, op: str, params: dict[str, Any]) -> Any:
        from agentcoop.timeline import (
            agents_view,
            event_detail,
            histogram,
            projects_view,
            query_events,
            rules_view,
            status_view,
        )
        from agentcoop.ui import files_view
        from agentcoop.wizard import add_project_flow, change_driver, helper_add, helper_remove

        if op == "projects":
            return {"projects": projects_view(self.hub.board)}
        if op == "events":
            return query_events(
                self.hub.board,
                project=params.get("project"),
                start=params.get("from") or params.get("start"),
                end=params.get("to") or params.get("end"),
                actors=params.get("actors"),
                kinds=params.get("kinds"),
                query=params.get("q") or params.get("query"),
                severity=params.get("severity"),
                cursor=params.get("cursor"),
                limit=int(params.get("limit") or 50),
                include_low=bool(params.get("low") or params.get("include_low")),
            )
        if op == "event":
            detail = event_detail(self.hub.board, str(params.get("id") or ""))
            if detail is None:
                raise CoopError("Event not found.")
            return detail
        if op == "histogram":
            return histogram(
                self.hub.board,
                project=params.get("project"),
                start=params.get("from") or params.get("start"),
                end=params.get("to") or params.get("end"),
                buckets=int(params.get("buckets") or 24),
            )
        if op == "agents":
            return {"agents": agents_view(self.hub.board, str(params.get("project") or ""))}
        if op == "rules":
            return rules_view(self.hub.board, str(params.get("project") or ""))
        if op == "status":
            return status_view(self.hub.board)
        if op == "files":
            return {"files": files_view(self.hub.board, str(params.get("project") or ""), self.hub.token)}
        if op == "subscribe":
            self.subscription = {
                "project": params.get("project"),
                "from": params.get("from"),
                "to": params.get("to"),
                "actors": params.get("actors"),
                "kinds": params.get("kinds"),
                "q": params.get("q"),
            }
            return {"subscribed": True}
        if op == "unsubscribe":
            self.subscription = None
            return {"subscribed": False}
        if op == "resume":
            last_seq = int(params.get("seq") or 0)
            missed, reason = self.hub.replay_since(last_seq)
            if reason is not None:
                raise CoopError(reason)
            return {"messages": missed, "seq": self.hub.seq}
        if op in ("driver", "helper_add", "helper_remove", "project_add", "shutdown"):
            if self.readonly:
                raise CoopError("Phone sessions are read-only. Use Manage on the computer.")
        if op == "driver":
            text = change_driver(params["project"], params["agent"], yes=True)
            return {"text": text}
        if op == "helper_add":
            text = helper_add(params["project"], params["agent"], params.get("note") or "helps the driver")
            return {"text": text}
        if op == "helper_remove":
            text = helper_remove(params["project"], params["agent"])
            return {"text": text}
        if op == "project_add":
            text = add_project_flow(
                path=Path(params["path"]),
                name=params.get("name"),
                driver=params.get("driver"),
                helpers=params.get("helpers") or {},
                yes=True,
                gitignore=True,
            )
            return {"text": text}
        if op == "shutdown":
            threading.Thread(target=self._shutdown_server, daemon=True).start()
            return {"text": "Closing."}
        raise CoopError("Unknown op: " + op)

    def _shutdown_server(self) -> None:
        server = getattr(self.hub, "server", None)
        if server is not None:
            server.shutdown()

    def _reply_error(self, req_id: Any, text: str) -> None:
        self._send_json({"id": req_id, "ok": False, "error": text})

    def _send_json(self, payload: dict[str, Any]) -> None:
        with self._send_lock:
            self.conn.send_text(json.dumps(payload, separators=(",", ":")))

    def _touch(self) -> None:
        self.last_seen = time.time()
        self.on_activity()


def _project_names(board: Path) -> list[str]:
    from agentcoop.projects import load_projects

    return sorted((load_projects(board).get("projects") or {}).keys())


def _event_fingerprint(event: dict[str, Any]) -> str:
    parts = [
        str(event.get("id") or ""),
        str(event.get("kind") or ""),
        str(event.get("title") or ""),
        str(event.get("summary") or ""),
        str(event.get("body") or "")[:200],
        str(event.get("order") or ""),
    ]
    return "|".join(parts)


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    from agentcoop.timeline import _public

    return _public(event, full=False)
