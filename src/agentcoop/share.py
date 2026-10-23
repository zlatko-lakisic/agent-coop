"""Copy files onto the board so every member can open the same bytes."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentcoop.errors import CoopError
from agentcoop.paths import project_dir
from agentcoop.projects import atomic_write_bytes, load_config, load_projects, mutate_projects, role_of
from agentcoop.safety import check_attachment, deny_reason

PROGRAM_SUFFIXES = {
    ".exe",
    ".dll",
    ".msi",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".lnk",
    ".pkg",
    ".command",
    ".sh",
}
ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz", ".7z", ".bz2"}
_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_RESERVED.update("COM" + str(n) for n in range(1, 10))
_RESERVED.update("LPT" + str(n) for n in range(1, 10))
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def share_root(board: Path, project: str) -> Path:
    """Return the folder that holds shared copies for one project."""
    return project_dir(board, project) / "files" / "shared"


def limits(board: Path) -> dict:
    """Return the share settings, using the defaults when a board has none."""
    config = load_config(board)
    return {
        "max_bytes": _positive(config.get("share_max_mb"), 200) * 1024 * 1024,
        "hard_bytes": _positive(config.get("share_hard_max_mb"), 4096) * 1024 * 1024,
        "keep_days": _positive(config.get("share_keep_days"), 30),
        "warn_bytes": _positive(config.get("share_warn_total_gb"), 10) * 1024 * 1024 * 1024,
        "max_mb": _positive(config.get("share_max_mb"), 200),
        "hard_mb": _positive(config.get("share_hard_max_mb"), 4096),
    }


def _positive(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return value


def human_size(size: int) -> str:
    """Return a short size such as 1.2 MB."""
    if size < 1024:
        return str(size) + " B"
    if size < 1024 * 1024:
        return str(round(size / 1024, 1)) + " KB"
    if size < 1024 * 1024 * 1024:
        return str(round(size / (1024 * 1024), 1)) + " MB"
    return str(round(size / (1024 * 1024 * 1024), 1)) + " GB"


def clean_filename(name: str) -> str:
    """Return a file name that is safe on Windows, macOS, and Linux."""
    text = str(name or "").replace("\\", "/")
    base = text.split("/")[-1]
    base = _BAD_NAME.sub("_", base).strip(" .")
    if not base or base in (".", ".."):
        base = "file"
    stem = base.split(".", 1)[0]
    if stem.upper() in _RESERVED:
        base = base + "_"
    return base


def unique_name(folder: Path, name: str) -> str:
    """Return name, or name.2, name.3, when that name is already there.

    The comparison ignores case on every system, so Notes.txt and notes.txt
    are not two files.
    """
    taken = set()
    if folder.is_dir():
        taken = {path.name.casefold() for path in folder.iterdir()}
    candidate = name
    number = 2
    while candidate.casefold() in taken:
        path = Path(name)
        candidate = path.stem + "." + str(number) + path.suffix
        number += 1
    return candidate


def sniff_media(path: Path, claimed: str = "") -> str:
    """Return a media type from the first bytes when they disagree with the name."""
    try:
        with path.open("rb") as handle:
            head = handle.read(64)
    except OSError:
        head = b""
    sniffed = _magic(head, path.name)
    guessed = claimed or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if sniffed and sniffed != guessed:
        return sniffed
    return guessed


def _magic(head: bytes, name: str) -> str:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "image/gif"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"RIFF") and len(head) >= 12 and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"ID3") or head.startswith(b"\xff\xfb"):
        return "audio/mpeg"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video/mp4"
    if head.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed"
    if head.startswith(b"\x7fELF"):
        return "application/x-elf"
    if head.startswith(b"MZ"):
        return "application/vnd.microsoft.portable-executable"
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return "application/x-mach-binary"
    if name.lower().endswith(".tar"):
        return "application/x-tar"
    return ""


def guess_kind(media: str, name: str, given: str = "") -> str:
    """Return the sender's kind, or a short hint from the media type."""
    if given:
        return given.strip()
    lowered = name.lower()
    if any(lowered.endswith(suffix) for suffix in ARCHIVE_SUFFIXES) or media in (
        "application/zip",
        "application/gzip",
        "application/x-tar",
        "application/x-7z-compressed",
    ):
        return "archive"
    if media.startswith("image/"):
        return "image"
    if media.startswith("video/"):
        return "video"
    if media.startswith("audio/"):
        return "audio"
    if media == "application/pdf" or media.startswith("text/"):
        return "document"
    return "other"


def is_program(path: Path) -> bool:
    """Return True when the file is a program or a script."""
    if path.suffix.lower() in PROGRAM_SUFFIXES or path.name.lower().endswith(".app"):
        return True
    try:
        with path.open("rb") as handle:
            head = handle.read(80)
    except OSError:
        return False
    if head.startswith(b"\x7fELF") or head.startswith(b"MZ"):
        return True
    if head[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
        return True
    text = head.split(b"\n", 1)[0]
    return text.startswith(b"#!")


def _index_path(board: Path, project: str) -> Path:
    return share_root(board, project) / "index.json"


def load_index(board: Path, project: str) -> dict:
    """Return the share index. A missing file is an empty index."""
    path = _index_path(board, project)
    if not path.is_file():
        return {"files": [], "permits": {}}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": [], "permits": {}}
    if not isinstance(data, dict):
        return {"files": [], "permits": {}}
    data.setdefault("files", [])
    data.setdefault("permits", {})
    return data


def save_index(board: Path, project: str, data: dict) -> None:
    """Write the share index by renaming a temp file."""
    path = _index_path(board, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    payload = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _by_hash(index: dict, digest: str) -> dict | None:
    for row in index.get("files") or []:
        if isinstance(row, dict) and row.get("sha256") == digest and Path(str(row.get("path") or "")).is_file():
            return row
    return None


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _copy_bytes(source: Path, dest_dir: Path, name: str) -> tuple[Path, str, int]:
    """Copy to a temp name, refuse if the source changes, then rename."""
    before = source.stat().st_size
    dest_dir.mkdir(parents=True, exist_ok=True)
    final_name = unique_name(dest_dir, clean_filename(name))
    tmp = dest_dir / ("." + final_name + ".partial")
    hasher = hashlib.sha256()
    written = 0
    try:
        with source.open("rb") as incoming, tmp.open("wb") as outgoing:
            while True:
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                outgoing.write(chunk)
                hasher.update(chunk)
                written += len(chunk)
        after = source.stat().st_size
        if after != before or written != before or tmp.stat().st_size != before:
            raise CoopError(source.name + " changed while it was being copied, so it was not shared.")
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    final = dest_dir / final_name
    os.replace(tmp, final)
    return final, hasher.hexdigest(), written


def _real_file(
    raw: str,
    *,
    project_path: Path,
    board: Path,
    project: str,
    extra_roots: list[str] | None = None,
) -> Path:
    path = Path(raw)
    config = load_config(board)
    roots = list(config.get("allowed_roots") or []) + list(extra_roots or [])
    if path.is_symlink():
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise CoopError("That link could not be opened.") from exc
        reason = check_attachment(
            str(target),
            project_path=project_path,
            board=board,
            project=project,
            allowed_roots=roots,
        )
        if reason:
            raise CoopError("That link points outside the folders this project is allowed to share.")
        if not stat.S_ISREG(target.stat().st_mode):
            raise CoopError("Only regular files can be shared.")
        return target
    reason = check_attachment(
        raw,
        project_path=project_path,
        board=board,
        project=project,
        allowed_roots=roots,
    )
    if reason:
        raise CoopError(reason)
    denied = deny_reason(path, board)
    if denied:
        raise CoopError(denied)
    return path.resolve()


def _text_secret(path: Path) -> str | None:
    """Return a refusal when a text file looks like it contains a key."""
    text_suffixes = {
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".log",
        ".py",
        ".js",
        ".ts",
        ".html",
        ".yml",
        ".yaml",
        ".toml",
        ".ini",
        ".cfg",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
        "",
    }
    if path.suffix.lower() not in text_suffixes:
        return None
    try:
        sample = path.read_text(encoding="utf-8", errors="ignore")[:20000]
    except OSError:
        return None
    from agentcoop.safety import secret_warning

    if secret_warning(sample):
        return path.name + " looks like it contains a password or API key, so it was not shared."
    return None


def _needs_person(path: Path, size: int, cap: dict) -> str:
    if size > cap["hard_bytes"]:
        return "hard"
    if is_program(path):
        return "program"
    if size > cap["max_bytes"]:
        return "large"
    return ""


def _ask_sentence(path: Path, size: int, cap: dict) -> str:
    return (
        path.name
        + " is "
        + human_size(size)
        + ", over the "
        + str(cap["max_mb"])
        + " MB share limit. Do not split it, compress it to get under the limit, "
        + "or attach it by its project path. Ask the person to allow this file with: "
        + "agentcoop share "
        + str(path)
        + " --over-limit"
    )


def _pending_path(board: Path, project: str) -> Path:
    return share_root(board, project) / "pending.json"


def load_pending(board: Path, project: str) -> list[dict]:
    """Return files waiting for the person. A missing file means none."""
    path = _pending_path(board, project)
    if not path.is_file():
        return []
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def save_pending(board: Path, project: str, rows: list[dict]) -> None:
    """Write the waiting list by renaming a temp file."""
    import json

    path = _pending_path(board, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(rows, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp = path.with_name("." + path.name + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def confirm_share(
    summary: str,
    *,
    board: Path,
    project: str,
    agent: str,
    name: str,
    size: int,
    sender: str,
    recipient: str,
) -> str:
    """Ask the person to allow one file. Return allow, cancel, or timeout.

    An agent has no flag that skips this. The confirmation is the only yes.
    """
    from agentcoop.confirm import TIMED_OUT, confirm_person

    row = {
        "name": name,
        "bytes": size,
        "sender": sender,
        "recipient": recipient,
        "summary": summary,
        "url": "",
    }
    pending = load_pending(board, project)
    pending.append(row)
    save_pending(board, project, pending)
    _refresh_share_views(board, project, agent)

    def on_url(url: str) -> None:
        row["url"] = url
        current = load_pending(board, project)
        for item in current:
            if item.get("name") == name and item.get("sender") == sender and not item.get("url"):
                item["url"] = url
                break
        else:
            current.append(row)
        save_pending(board, project, current)
        _refresh_share_views(board, project, agent)

    try:
        confirm_person(summary, expected="review", kind="review", asked_by=sender, on_url=on_url)
    except CoopError as exc:
        if str(exc) == TIMED_OUT:
            return "timeout"
        return "cancel"
    finally:
        left = [
            item
            for item in load_pending(board, project)
            if not (item.get("name") == name and item.get("sender") == sender)
        ]
        save_pending(board, project, left)
        _refresh_share_views(board, project, agent)
    return "allow"


def _refresh_share_views(board: Path, project: str, agent: str) -> None:
    try:
        from agentcoop.outbox import refresh_new_md_for_agent
        from agentcoop.statusfile import refresh_status

        refresh_new_md_for_agent(board, project, agent)
        refresh_status(board)
    except Exception:
        return


def pending_notes(board: Path, project: str, agent: str = "") -> list[str]:
    """Return lines for files waiting on the person."""
    notes = []
    for row in load_pending(board, project):
        if agent and row.get("sender") not in ("", agent):
            continue
        text = (
            "A file is waiting for approval: "
            + str(row.get("name") or "file")
            + " ("
            + human_size(int(row.get("bytes") or 0))
            + ") from "
            + str(row.get("sender") or "")
            + " to "
            + str(row.get("recipient") or "")
            + "."
        )
        if row.get("url"):
            text += " Open: " + str(row["url"])
        notes.append(text)
    return notes


def place_file(
    board: Path,
    project: str,
    source: str,
    *,
    sender: str,
    recipient: str,
    message_id: str,
    description: str = "",
    kind: str = "",
    media_type: str = "",
    alt_of: str = "",
    over_limit: bool = False,
    ask: bool = False,
    confirm=None,
) -> dict:
    """Copy one file into the shared folder and return its record.

    ask=True uses the person's confirmation for a large file. Without it, a
    large file is refused. A program always asks. A private file never asks.
    """
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    project_path = Path(str(record.get("path") or ""))
    from agentcoop.paths import outbox_dir

    real = _real_file(
        source,
        project_path=project_path,
        board=board,
        project=project,
        extra_roots=[str(outbox_dir(board, project, sender))],
    )
    secret = _text_secret(real)
    if secret:
        raise CoopError(secret)
    cap = limits(board)
    size = real.stat().st_size
    reason = _needs_person(real, size, cap)
    if reason == "hard":
        raise CoopError(
            real.name
            + " is "
            + human_size(size)
            + ", over "
            + str(cap["hard_mb"])
            + " MB. It is too big to share through the board."
        )
    digest, _ = _hash_file(real)
    index = load_index(board, project)
    existing = _by_hash(index, digest)
    if existing is not None:
        current = str(existing.get("message") or "")
        if message_id and (current in ("", "catalog") or current.startswith("outbox-")):
            existing["message"] = message_id
            existing["recipient"] = recipient
            if description:
                existing["description"] = description
            save_index(board, project, index)
        info = dict(existing)
        info["description"] = description or str(existing.get("description") or "")
        info["alt_of"] = alt_of or str(existing.get("alt_of") or "")
        info["source_path"] = str(real)
        return _public_info(info)
    if reason == "large" and not over_limit and not ask:
        raise CoopError(_ask_sentence(real, size, cap))
    if reason in ("large", "program"):
        summary = (
            sender
            + " wants to share "
            + real.name
            + " ("
            + human_size(size)
            + ") with "
            + recipient
            + "."
        )
        if reason == "program":
            summary += " It is a program or a script, so it is held until you allow it."
        else:
            summary += " It is over the " + str(cap["max_mb"]) + " MB share limit."
        asker = confirm or (
            lambda text: confirm_share(
                text,
                board=board,
                project=project,
                agent=sender,
                name=real.name,
                size=size,
                sender=sender,
                recipient=recipient,
            )
        )
        answer = asker(summary)
        if answer == "timeout":
            raise CoopError("Nobody allowed " + real.name + ", so it was not shared.")
        if answer != "allow":
            raise CoopError(real.name + " was not allowed, so it was not shared.")
    folder = share_root(board, project) / message_id
    copied, copied_hash, copied_size = _copy_bytes(real, folder, real.name)
    media = media_type or sniff_media(copied)
    chosen_kind = guess_kind(media, copied.name, kind)
    info = {
        "name": copied.name,
        "path": str(copied),
        "source_path": str(real),
        "bytes": copied_size,
        "sha256": copied_hash,
        "media_type": media,
        "kind": chosen_kind,
        "description": description,
        "sender": sender,
        "recipient": recipient,
        "message": message_id,
        "pinned": False,
        "archived_on": "",
        "shared_on": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if alt_of:
        info["alt_of"] = alt_of
    if reason == "program":
        info["held"] = "program"
    if chosen_kind == "archive" or copied.suffix.lower() in ARCHIVE_SUFFIXES:
        info["contents"] = "not checked"
    index = load_index(board, project)
    index["files"].append(info)
    save_index(board, project, index)
    return _public_info(info)


def _public_info(info: dict) -> dict:
    kept = {
        "name": info.get("name") or "",
        "path": info.get("path") or "",
        "source_path": info.get("source_path") or "",
        "bytes": int(info.get("bytes") or 0),
        "sha256": info.get("sha256") or "",
        "media_type": info.get("media_type") or "",
        "kind": info.get("kind") or "other",
        "description": info.get("description") or "",
    }
    if info.get("alt_of"):
        kept["alt_of"] = info["alt_of"]
    if info.get("contents"):
        kept["contents"] = info["contents"]
    if info.get("held"):
        kept["held"] = info["held"]
    return kept


def share_ahead(
    board: Path,
    project: str,
    source: str,
    *,
    sender: str,
    description: str = "",
    kind: str = "",
    over_limit: bool = False,
    confirm=None,
) -> str:
    """Copy a file before a message exists and return the shared path."""
    info = place_file(
        board,
        project,
        source,
        sender=sender,
        recipient=sender,
        message_id="catalog",
        description=description,
        kind=kind,
        over_limit=over_limit,
        ask=over_limit,
        confirm=confirm,
    )
    return str(info["path"])


def accepts_of(board: Path, project: str, agent: str) -> list[str] | None:
    """Return accept patterns, or None when the agent has not declared any."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    profiles = record.get("profiles") if isinstance(record.get("profiles"), dict) else {}
    profile = profiles.get(agent) if isinstance(profiles.get(agent), dict) else {}
    patterns = profile.get("accepts")
    if isinstance(patterns, list) and patterns:
        return [str(item) for item in patterns if str(item).strip()]
    from agentcoop.projects import load_agents

    agent_record = (load_agents(board).get("agents") or {}).get(agent) or {}
    patterns = agent_record.get("accepts")
    if isinstance(patterns, list) and patterns:
        return [str(item) for item in patterns if str(item).strip()]
    return None


def remember_accepts(board: Path, agent: str, patterns: list[str]) -> None:
    """Save what an agent accepts on its own record. Missing stays unknown."""
    from agentcoop.projects import load_agents, save_agents

    cleaned = [item.strip() for item in patterns if item.strip()]
    agents = load_agents(board)
    row = (agents.get("agents") or {}).get(agent)
    if not isinstance(row, dict):
        raise CoopError(agent + " is not connected. Run: agentcoop agents")
    row["accepts"] = cleaned
    save_agents(board, agents)


def set_accepts(board: Path, project: str, agent: str, patterns: list[str]) -> None:
    """Save what one agent accepts on this project and on the agent record."""
    cleaned = [item.strip() for item in patterns if item.strip()]

    def change(data: dict) -> None:
        record = (data.get("projects") or {}).get(project)
        if record is None:
            raise CoopError("There is no project named " + project + ".")
        if not role_of(record, agent):
            raise CoopError(agent + " is not in " + project + ".")
        profiles = record.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
            record["profiles"] = profiles
        current = profiles.get(agent) if isinstance(profiles.get(agent), dict) else {}
        updated = dict(current)
        updated["accepts"] = cleaned
        profiles[agent] = updated

    mutate_projects(board, change)
    remember_accepts(board, agent, cleaned)


def matches_accept(media: str, patterns: list[str]) -> bool:
    """Return True when a media type fits one pattern. */* fits everything."""
    for pattern in patterns:
        if pattern == "*/*":
            return True
        if pattern.endswith("/*"):
            if media.startswith(pattern[:-1]):
                return True
        elif media == pattern:
            return True
    return False


def mismatch_note(board: Path, project: str, recipient: str, media: str) -> str:
    """Return a one-line note, or empty when nothing is declared or it matches."""
    patterns = accepts_of(board, project, recipient)
    if not patterns or matches_accept(media, patterns):
        return ""
    shown = ", ".join(patterns)
    return recipient + " does not list " + _family(media) + " in what it accepts (" + shown + ")."


def _family(media: str) -> str:
    if media.endswith("/*") or "/" not in media:
        return media
    return media.split("/", 1)[0] + "/*"


def file_clause(infos: list[dict]) -> str:
    """Return the files half of a Sent to line."""
    if not infos:
        return ""
    bits = []
    for info in infos:
        label = str(info.get("kind") or "file")
        bits.append(str(info.get("name") or "file") + " (" + label + ", " + human_size(int(info.get("bytes") or 0)) + ")")
    noun = "file" if len(bits) == 1 else "files"
    return str(len(bits)) + " " + noun + ": " + ", ".join(bits)


def group_lines(infos: list[dict], notes: dict[str, str]) -> list[str]:
    """Return check lines, with other forms grouped under the original."""
    alts: dict[str, list[dict]] = {}
    for info in infos:
        parent = str(info.get("alt_of") or "")
        if parent:
            alts.setdefault(parent, []).append(info)
    lines = []
    grouped: set[str] = set()
    for info in infos:
        if info.get("alt_of"):
            continue
        lines.append(_one_line(info, notes.get(str(info.get("path") or ""), "")))
        for alt in alts.get(str(info.get("sha256") or ""), []):
            grouped.add(str(alt.get("sha256") or id(alt)))
            lines.append("      other form: " + _one_line(alt, notes.get(str(alt.get("path") or ""), "")))
    for info in infos:
        parent = str(info.get("alt_of") or "")
        if not parent:
            continue
        if str(info.get("sha256") or id(info)) in grouped:
            continue
        lines.append("      other form: " + _one_line(info, notes.get(str(info.get("path") or ""), "")))
    return lines


def _one_line(info: dict, note: str) -> str:
    text = str(info.get("name") or info.get("path") or "file")
    text += " (" + str(info.get("kind") or "file") + ", " + human_size(int(info.get("bytes") or 0)) + ")"
    if info.get("description"):
        text += " " + str(info["description"])
    if info.get("contents"):
        text += " (" + str(info["contents"]) + ")"
    if note:
        text += " " + note
    return text


def list_rows(board: Path, project: str, now: datetime | None = None) -> list[str]:
    """Return one line per shared file."""
    moment = now or datetime.now(timezone.utc)
    lines = []
    for row in load_index(board, project).get("files") or []:
        if not isinstance(row, dict) or not Path(str(row.get("path") or "")).is_file():
            continue
        age = _age(str(row.get("shared_on") or ""), moment)
        pinned = " pinned" if row.get("pinned") else ""
        lines.append(
            str(row.get("name"))
            + " "
            + human_size(int(row.get("bytes") or 0))
            + " "
            + str(row.get("kind") or "file")
            + " from "
            + str(row.get("sender") or "")
            + " message "
            + str(row.get("message") or "")
            + " "
            + age
            + pinned
        )
    return lines


def _age(stamp: str, now: datetime) -> str:
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "age unknown"
    days = int((now - then).total_seconds() // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day"
    return str(days) + " days"


def set_pin(board: Path, project: str, token: str, *, pinned: bool, actor: str) -> str:
    """Pin or unpin a file. The sender or the driver may do this."""
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    index = load_index(board, project)
    found = None
    for row in index.get("files") or []:
        if not isinstance(row, dict):
            continue
        if token == row.get("sha256") or token == row.get("path") or token == row.get("name"):
            found = row
            break
    if found is None:
        raise CoopError("There is no shared file " + token + ".")
    if actor != found.get("sender") and actor != record.get("driver"):
        raise CoopError("Only the sender or the driver can pin a shared file.")
    found["pinned"] = bool(pinned)
    save_index(board, project, index)
    word = "pinned" if pinned else "unpinned"
    return str(found.get("name")) + " is " + word + "."


def total_bytes(board: Path, project: str) -> int:
    """Return the size of the shared folder."""
    root = share_root(board, project)
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def status_lines(board: Path, project: str) -> list[str]:
    """Return the shared-file lines for STATUS.md and agentcoop status."""
    cap = limits(board)
    lines = ["Shared files: up to " + str(cap["max_mb"]) + " MB each. " + human_size(total_bytes(board, project)) + " in this project."]
    if total_bytes(board, project) >= cap["warn_bytes"]:
        lines.append("Shared files are past the warning size. Nothing was deleted.")
    lines.extend(pending_notes(board, project))
    rows = [row for row in load_index(board, project).get("files") or [] if isinstance(row, dict)]
    for row in rows[-5:]:
        lines.append(
            "File: "
            + str(row.get("name"))
            + " ("
            + str(row.get("kind") or "file")
            + ", "
            + human_size(int(row.get("bytes") or 0))
            + ") from "
            + str(row.get("sender") or "")
            + " to "
            + str(row.get("recipient") or "")
        )
    return lines


def _message_paths(board: Path, project: str) -> list[tuple[Path, bool]]:
    """Return message files and whether each one is in the archive."""
    root = project_dir(board, project)
    found = []
    if not root.is_dir():
        return found
    for path in root.rglob("*.json"):
        if path.name == "index.json":
            continue
        archived = "archive" in path.parts
        if "sent" in path.parts and not archived:
            continue
        if path.name in ("index.json", "pending.json"):
            continue
        found.append((path, archived))
    return found


def _referenced(board: Path, project: str, file_path: str) -> tuple[bool, bool]:
    """Return (referenced by a live message, referenced only from the archive)."""
    import json

    live = False
    archived = False
    for path, is_archived in _message_paths(board, project):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        attachments = data.get("attachments") or []
        infos = data.get("attachment_info") or []
        paths = [str(item) for item in attachments]
        paths.extend(str(item.get("path")) for item in infos if isinstance(item, dict))
        if file_path not in paths:
            continue
        if is_archived:
            archived = True
        else:
            live = True
    return live, archived


def prune(board: Path, project: str, now: datetime | None = None) -> list[str]:
    """Remove shared files kept past the limit after their messages were archived."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cap = limits(board)
    index = load_index(board, project)
    kept = []
    removed = []
    for row in index.get("files") or []:
        if not isinstance(row, dict):
            continue
        path = Path(str(row.get("path") or ""))
        if row.get("pinned"):
            row["archived_on"] = ""
            kept.append(row)
            continue
        live, archived = _referenced(board, project, str(path))
        if live or not archived:
            row["archived_on"] = ""
            kept.append(row)
            continue
        stamp = str(row.get("archived_on") or "")
        if not stamp:
            row["archived_on"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
            kept.append(row)
            continue
        try:
            then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            row["archived_on"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
            kept.append(row)
            continue
        if moment - then < timedelta(days=cap["keep_days"]):
            kept.append(row)
            continue
        if path.is_file():
            path.unlink()
        removed.append(str(path))
    index["files"] = kept
    save_index(board, project, index)
    return removed


def prune_due(board: Path, now: datetime | None = None) -> None:
    """Prune every project at most once a day."""
    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y-%m-%d")
    marker = board / ".state" / "share-prune"
    if marker.is_file():
        try:
            if marker.read_text(encoding="utf-8").strip() == stamp:
                return
        except OSError:
            pass
    for name in (load_projects(board).get("projects") or {}):
        prune(board, name, now=moment)
    marker.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(marker, (stamp + "\n").encode("utf-8"))


def _outbox_files_dir(answer_path: Path) -> Path | None:
    """Return the desktop files folder beside an answer, including after a claim."""
    if not answer_path.name.endswith(".md"):
        return None
    stem = answer_path.name[: -len(".md")]
    parents = [answer_path.parent]
    if answer_path.parent.name == "processing":
        parents.append(answer_path.parent.parent)
    for parent in parents:
        folder = parent / (stem + ".files")
        if folder.is_dir():
            return folder
    return None


def read_files_json(path: Path) -> dict:
    """Read files.json, including a UTF-8 BOM and Windows newlines."""
    import json

    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    data = json.loads(text)
    return data if isinstance(data, dict) else {}


def take_outbox_files(
    board: Path,
    project: str,
    agent: str,
    answer_path: Path,
    *,
    recipient: str,
    confirm=None,
) -> tuple[list[str], list[dict], str]:
    """Copy a desktop files folder. Rejected files do not stop the answer."""
    folder = _outbox_files_dir(answer_path)
    if folder is None:
        return [], [], ""
    meta: dict = {}
    sidecar = folder / "files.json"
    if sidecar.is_file():
        try:
            meta = read_files_json(sidecar)
        except (OSError, ValueError):
            meta = {}
    paths: list[str] = []
    infos: list[dict] = []
    notes: list[str] = []
    from agentcoop.paths import outbox_rejected_dir

    rejected = outbox_rejected_dir(board, project, agent)
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name == "files.json" or path.name.startswith("."):
            continue
        described = meta.get(path.name) if isinstance(meta.get(path.name), dict) else {}
        try:
            info = place_file(
                board,
                project,
                str(path),
                sender=agent,
                recipient=recipient,
                message_id="outbox-" + answer_path.stem,
                description=str(described.get("description") or ""),
                kind=str(described.get("kind") or ""),
                over_limit=True,
                ask=True,
                confirm=confirm,
            )
        except CoopError as exc:
            rejected.mkdir(parents=True, exist_ok=True)
            dest = rejected / path.name
            if dest.exists():
                dest = rejected / (path.stem + ".2" + path.suffix)
            try:
                os.replace(path, dest)
            except OSError:
                dest = path
            reason_path = dest.with_name(dest.name + ".reason.txt")
            reason_path.write_text(str(exc) + "\n", encoding="utf-8")
            notes.append(path.name + " was held back: " + str(exc))
            continue
        paths.append(str(info["path"]))
        infos.append(info)
    sentence = ""
    if notes:
        sentence = "Some files were held back. " + " ".join(notes)
    return paths, infos, sentence
