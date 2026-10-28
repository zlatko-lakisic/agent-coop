"""Claude desktop as an outbox helper: skill zip, folders, and prompts."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from agentcoop import __version__
from agentcoop.errors import CoopError
from agentcoop.install import read_template
from agentcoop.paths import (
    ensure_outbox_dirs,
    inbox_new_md,
    native_path,
    outbox_dir,
    outbox_rejected_dir,
    outbox_sent_dir,
    private_dir,
    project_dir,
)
from agentcoop.projects import load_agents, load_projects
from agentcoop.wizard import connect_one, helper_add, helper_remove

DEFAULT_AGENT = "claude_desktop"
DEFAULT_NOTE = "helps the driver reason"
GENERAL_SKILL = "agent-coop"
OLD_GENERAL_SKILL = "claude-desktop"
RENAME_EXPORT = (
    "Re-export the Claude desktop skill with agentcoop desktop prepare and upload it again. "
    "The skill has a new name."
)


PROMPT_VERSION = "v2"


def scheduled_prompt(project: str, agent: str) -> str:
    """Return the exact prompt for a Claude scheduled task.

    The check steps live in the Agent Co-op skill. This sentence does not
    repeat them. Prompt v2 is the marker for this wording.
    """
    return (
        "Check the Agent Co-op board for the "
        + project
        + " project as "
        + agent
        + ", using the Agent Co-op skill. Prompt "
        + PROMPT_VERSION
        + "."
    )


def skill_folder_name(project: str) -> str:
    """Return the skill folder and YAML name for one project."""
    return "agent-coop-" + project


def answer_path_for(outbox: str) -> str:
    """Return the answer file path the skill should write, with a message-id slot."""
    sep = "\\" if "\\" in outbox else "/"
    return outbox + sep + "<message id>.<UTC timestamp>.md"


def export_zip_path(board: Path, project: str, agent: str, dest_dir: Path | None = None) -> Path:
    """Return the skill zip path for this helper and project."""
    folder = Path(dest_dir) if dest_dir is not None else board / "exports"
    return folder / (agent + "-" + project + ".zip")


def export_version_path(zip_path: Path) -> Path:
    """Return the sidecar that records which tool version built the zip."""
    return zip_path.with_name(zip_path.name + ".version")


def export_version_text(board: Path, project: str, agent: str) -> str:
    """Return the sidecar version string, or empty when the sidecar is missing."""
    path = export_version_path(export_zip_path(board, project, agent))
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def fill_desktop_skill(values: dict[str, str]) -> str:
    """Fill claude-desktop-skill.md.tmpl from a placeholder map."""
    text = read_template("claude-desktop-skill.md.tmpl")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def skill_placeholders(board: Path, project: str, agent: str) -> dict[str, str]:
    """Build native-path placeholders for the Claude desktop skill."""
    record = (load_projects(board).get("projects") or {}).get(project)
    if record is None:
        raise CoopError("There is no project named " + project + ". Run: agentcoop project list")
    if agent not in (record.get("helpers") or {}):
        raise CoopError(agent + " is not a helper on " + project + ".")
    outbox = native_path(outbox_dir(board, project, agent).resolve())
    return {
        "AGENT_NAME": agent,
        "PROJECT": project,
        "DRIVER": str(record.get("driver") or ""),
        "NEW_MD": native_path(inbox_new_md(board, project, agent).resolve()),
        "OUTBOX": outbox,
        "OUTBOX_SENT": native_path(outbox_sent_dir(board, project, agent).resolve()),
        "OUTBOX_REJECTED": native_path(outbox_rejected_dir(board, project, agent).resolve()),
        "PROJECT_BOARD": native_path(project_dir(board, project).resolve()),
        "PROJECT_PATH": native_path(Path(str(record.get("path") or "")).resolve()),
        "ANSWER_PATH": answer_path_for(outbox),
        "SKILL_NAME": skill_folder_name(project),
    }


def _packaged_skill_text() -> str:
    """Read the skill shipped inside this package.

    importlib.resources is tried first. Python 3.9 cannot open a folder whose
    name contains a hyphen when the package is inside a zipapp, so the same
    archive member is read directly in that case.
    """
    from importlib import resources
    import zipfile

    try:
        text = (
            resources.files("agentcoop")
            .joinpath("data", GENERAL_SKILL, "SKILL.md")
            .read_text(encoding="utf-8")
        )
        if text.strip():
            return text
    except (FileNotFoundError, OSError, TypeError, ValueError, AttributeError, KeyError):
        pass
    import agentcoop

    location = getattr(agentcoop, "__file__", "")
    normalized = location.replace("\\", "/")
    marker = "/agentcoop/__init__.py"
    index = normalized.rfind(marker)
    if index <= 0:
        return ""
    archive = location[:index].rstrip("/\\")
    try:
        if not zipfile.is_zipfile(archive):
            return ""
        member = "agentcoop/data/" + GENERAL_SKILL + "/SKILL.md"
        with zipfile.ZipFile(archive) as zipped:
            return zipped.read(member).decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile, UnicodeError):
        return ""


def general_skill_text() -> str:
    """Return the Claude desktop skill that works on any computer.

    The package copy is read first, so a zipapp can see it. A frozen build
    and a source checkout are fallbacks.
    """
    text = _packaged_skill_text()
    if text.strip():
        return text
    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "skills" / GENERAL_SKILL / "SKILL.md")
    candidates.append(Path(__file__).resolve().parents[2] / "skills" / GENERAL_SKILL / "SKILL.md")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise CoopError("The Claude desktop skill file is missing.")


def prepare_skill(board: Path, dest_dir: Path | None = None) -> Path:
    """Write the skill zip. The folder name matches GENERAL_SKILL."""
    text = general_skill_text()
    payload = text if text.endswith("\n") else text + "\n"
    folder = Path(dest_dir) if dest_dir is not None else board / "exports"
    private_dir(folder)
    zip_path = folder / (GENERAL_SKILL + ".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = __version__.encode("utf-8")
        archive.writestr(GENERAL_SKILL + "/SKILL.md", payload)
    sidecar = zip_path.with_name(zip_path.name + ".version")
    sidecar.write_text(__version__ + "\n", encoding="utf-8")
    return zip_path


def export_skill(
    board: Path,
    project: str,
    agent: str,
    dest_dir: Path | None = None,
) -> Path:
    """Write {agent}-{project}.zip with agent-coop-<project>/SKILL.md."""
    values = skill_placeholders(board, project, agent)
    text = fill_desktop_skill(values)
    zip_path = export_zip_path(board, project, agent, dest_dir=dest_dir)
    private_dir(zip_path.parent)
    payload = text if text.endswith("\n") else text + "\n"
    inner = skill_folder_name(project) + "/SKILL.md"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.comment = __version__.encode("utf-8")
        archive.writestr(inner, payload)
    sidecar = export_version_path(zip_path)
    sidecar.write_text(__version__ + "\n", encoding="utf-8")
    return zip_path


def desktop_add(
    board: Path,
    project: str,
    note: str | None = None,
    agent: str = DEFAULT_AGENT,
) -> str:
    """Connect the desktop helper, export its skill, and return setup instructions."""
    agents = load_agents(board).get("agents") or {}
    if agent not in agents:
        connect_one("claude-desktop", agent_name=agent, yes=True)
    helper_add(project, agent, note or DEFAULT_NOTE)
    ensure_outbox_dirs(board, project, agent)
    from agentcoop.outbox import write_new_md

    write_new_md(board, project, agent)
    zip_path = prepare_skill(board)
    record = (load_projects(board).get("projects") or {}).get(project) or {}
    board_folder = native_path(project_dir(board, project).resolve())
    project_folder = native_path(Path(str(record.get("path") or "")).resolve())
    prompt = scheduled_prompt(project, agent)
    lines = [
        "Grant Claude two folders.",
        "",
        "1. Read and write: " + board_folder,
        ".agent_board is a hidden folder. Type this path; a file picker can miss it.",
        "",
        "2. Read only: " + project_folder,
        "",
        "Upload the skill: Customize > Skills, then + Create skill, then Upload a skill.",
        "The zip is: " + native_path(zip_path.resolve()),
        "",
        "Scheduled prompt:",
        prompt,
        "",
        "Test: ask the driver to send a question with progress. The next check should "
        "show the answer. On Windows there is no console window.",
    ]
    return "\n".join(lines) + "\n"


def desktop_remove(board: Path, project: str, agent: str = DEFAULT_AGENT) -> str:
    """Remove the desktop helper. Keep outbox/sent/."""
    text = helper_remove(project, agent)
    new_md = inbox_new_md(board, project, agent)
    if new_md.is_file():
        new_md.unlink()
    zip_path = export_zip_path(board, project, agent)
    sidecar = export_version_path(zip_path)
    if zip_path.is_file():
        zip_path.unlink()
    if sidecar.is_file():
        sidecar.unlink()
    return text
