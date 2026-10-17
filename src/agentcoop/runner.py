"""Run a local model as a helper. It only replies. It never starts a chat."""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

from agentcoop.core import check_inbox, mark_done, projects_for_check, send
from agentcoop.errors import CoopError
from agentcoop.paths import board_root, files_dir
from agentcoop.projects import load_agents, load_config, load_projects, require_board
from agentcoop.safety import open_attachment

IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 32 * 1024
MAX_BACKOFF = 300


def build_prompt(subject: str, body: str, notes: list[str]) -> str:
    """Build the user prompt from a message and its attachment notes."""
    lines = ["Subject: " + subject, "Body:", body]
    if notes:
        lines.append("")
        lines.extend(notes)
    return "\n".join(lines)


def read_attachment_notes(
    attachments: list[str],
    *,
    project_path: Path,
    board: Path,
    project: str,
    allowed_roots: list[str],
) -> tuple[list[str], list[tuple[str, bytes]]]:
    """Return prompt notes and image bytes. Blocked files are described, not opened."""
    notes: list[str] = []
    images: list[tuple[str, bytes]] = []
    kwargs = {
        "project_path": project_path,
        "board": board,
        "project": project,
        "allowed_roots": allowed_roots,
    }
    for raw in attachments:
        path = Path(raw)
        mime = IMAGE_SUFFIXES.get(path.suffix.lower())
        try:
            if mime:
                data = open_attachment(raw, max_bytes=MAX_IMAGE_BYTES, **kwargs)
            else:
                data = open_attachment(raw, max_bytes=MAX_TEXT_BYTES, clip=True, **kwargs)
        except CoopError as exc:
            notes.append("Skipped " + path.name + ": " + str(exc))
            continue
        if mime:
            images.append((mime, data))
            notes.append("Attached image: " + path.name)
            continue
        clipped = False
        try:
            clipped = path.resolve().stat().st_size > MAX_TEXT_BYTES
        except OSError:
            clipped = len(data) >= MAX_TEXT_BYTES
        text = data.decode("utf-8", errors="replace")
        if clipped:
            notes.append(path.name + " was cut at 32 KB.")
        notes.append("File " + path.name + ":\n" + text)
    return notes, images


def ollama_payload(model: str, prompt: str, images: list[tuple[str, bytes]]) -> dict:
    """Build an Ollama /api/chat body. Images are raw base64."""
    message: dict = {"role": "user", "content": prompt}
    if images:
        message["images"] = [base64.b64encode(data).decode("ascii") for _mime, data in images]
    return {"model": model, "messages": [message], "stream": False}


def openai_payload(model: str, prompt: str, images: list[tuple[str, bytes]]) -> dict:
    """Build an OpenAI-compatible chat body. Images are data URLs."""
    content: list[dict] = [{"type": "text", "text": prompt}]
    for mime, data in images:
        encoded = base64.b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": "data:" + mime + ";base64," + encoded}})
    return {"model": model, "messages": [{"role": "user", "content": content}]}


def parse_model_text(api: str, payload: dict) -> str:
    """Read the assistant text from either API."""
    if api == "openai":
        return str(payload["choices"][0]["message"]["content"])
    return str(payload["message"]["content"])


def post_chat(api: str, host: str, body: dict, opener=None, timeout: int = 120) -> dict:
    """POST a chat request and return the JSON object."""
    opener = opener or urllib.request.urlopen
    base = host.rstrip("/")
    url = base + ("/v1/chat/completions" if api == "openai" else "/api/chat")
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _give_up(board: Path, agent: str, project: str, message, reason: str, log) -> None:
    """Tell the driver this message will not be answered, then mark it done."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    driver = record.get("driver") or ""
    subject = "Could not answer: " + message.subject[:80]
    body = (
        agent
        + " could not send its answer. Reason: "
        + reason
        + ". The model's answer was not sent."
    )
    if driver:
        try:
            send(
                board,
                project=project,
                sender=agent,
                recipient=driver,
                subject=subject,
                body=body,
                type="status",
                re=message.id,
            )
        except (CoopError, OSError, ValueError, KeyError) as exc:
            log(agent + " could not tell the driver about " + message.id + ". " + str(exc))
    mark_done(board, project, agent, [message.id])


def reply_to_message(
    board: Path,
    *,
    agent: str,
    api: str,
    host: str,
    model: str,
    project: str,
    message,
    system_prompt: str = "",
    opener=None,
) -> None:
    """Send one answer and mark the message done. Raise if the model call fails."""
    config = load_config(board)
    from agentcoop.projects import load_projects

    record = load_projects(board)["projects"][project]
    notes, images = read_attachment_notes(
        list(message.attachments),
        project_path=Path(record["path"]),
        board=board,
        project=project,
        allowed_roots=list(config.get("allowed_roots") or []),
    )
    prompt = build_prompt(message.subject, message.body, notes)
    if system_prompt:
        prompt = system_prompt.strip() + "\n\n" + prompt
    body = openai_payload(model, prompt, images) if api == "openai" else ollama_payload(model, prompt, images)
    payload = post_chat(api, host, body, opener=opener)
    answer = parse_model_text(api, payload)
    limit = int(config.get("body_limit_bytes") or 16384)
    attachments: list[str] = []
    if len(answer.encode("utf-8")) > limit:
        folder = files_dir(board, project, agent)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (message.id + "-answer.md")
        path.write_text(answer, encoding="utf-8")
        attachments.append(str(path))
        answer = "The answer is in the attached file."
    send(
        board,
        project=project,
        sender=agent,
        recipient=message.sender,
        subject="Re: " + message.subject,
        body=answer,
        type="answer",
        re=message.id,
        attachments=attachments,
    )
    mark_done(board, project, agent, [message.id])


def run_runner(
    *,
    agent: str,
    api: str | None = None,
    host: str | None = None,
    model: str | None = None,
    interval: int = 5,
    once: bool = False,
    system_prompt: str = "",
    opener=None,
    sleep_fn=None,
    log=print,
) -> int:
    """Check for mail and answer it until stopped."""
    board = board_root()
    require_board(board)
    agents = load_agents(board)
    record = (agents.get("agents") or {}).get(agent)
    if record is None:
        raise CoopError(agent + " is not connected. Run: agentcoop connect ollama --model llava")
    runner = record.get("runner") or {}
    if record.get("tool") != "runner" and not runner:
        raise CoopError(agent + " is not a local model runner.")
    chosen_api = api or runner.get("api") or "ollama"
    chosen_host = host or runner.get("host") or "http://localhost:11434"
    chosen_model = model or runner.get("model") or ""
    if not chosen_model:
        raise CoopError("Name the model. Example: agentcoop runner --as ollama_llava --model llava")
    pause = sleep_fn or time.sleep
    backoff = max(interval, 1)
    failures: dict[str, int] = {}
    while True:
        failed = False
        try:
            names = projects_for_check(board, agent, None, Path.cwd(), True)
            for project in names:
                report = check_inbox(board, project, agent)
                if report.role != "helper":
                    raise CoopError("Runners are helpers only. " + agent + " is the driver for " + project + ".")
                for item in report.messages:
                    message = item.message
                    if item.violation:
                        log("Ignoring out of protocol message " + message.id)
                        mark_done(board, project, agent, [message.id])
                        continue
                    if message.type not in ("question", "task") and not message.needs_reply:
                        mark_done(board, project, agent, [message.id])
                        log("Noted " + message.id + " (" + message.type + "). No answer needed.")
                        continue
                    try:
                        reply_to_message(
                            board,
                            agent=agent,
                            api=chosen_api,
                            host=chosen_host,
                            model=chosen_model,
                            project=project,
                            message=message,
                            system_prompt=system_prompt,
                            opener=opener,
                        )
                        failures.pop(message.id, None)
                    except CoopError as exc:
                        _give_up(board, agent, project, message, str(exc), log)
                    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                        count = failures.get(message.id, 0) + 1
                        failures[message.id] = count
                        if count >= 10:
                            _give_up(
                                board,
                                agent,
                                project,
                                message,
                                "the model did not answer after 10 tries: " + str(exc),
                                log,
                            )
                            failures.pop(message.id, None)
                        else:
                            failed = True
                            log(agent + " could not answer " + message.id + ". " + str(exc))
        except KeyboardInterrupt:
            return 0
        if once:
            return 1 if failed else 0
        if failed:
            backoff = min(max(backoff * 2, interval), MAX_BACKOFF)
        else:
            backoff = max(interval, 1)
        try:
            pause(backoff)
        except KeyboardInterrupt:
            return 0
