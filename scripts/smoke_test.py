"""Set up a temporary board and check the installed command.

Later steps use <board>/bin/agentcoop, not the copy in this checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path


def _agentcoopw_name(name: str) -> str:
    """Return the no-console sibling name next to a Windows binary."""
    if "agentcoopw" in name.lower():
        return name
    if "agentcoop-" in name:
        return name.replace("agentcoop-", "agentcoopw-", 1)
    return name.replace("agentcoop", "agentcoopw", 1)


def unpack_windows_zip(zip_path: Path, dest: Path) -> tuple[Path, Path]:
    """Unpack agentcoop.exe and agentcoopw.exe from the Windows zip."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    console = dest / "agentcoop.exe"
    quiet = dest / "agentcoopw.exe"
    if not console.is_file() or not quiet.is_file():
        raise SystemExit("The Windows zip must contain agentcoop.exe and agentcoopw.exe side by side.")
    return console, quiet


def _wants_tk(binary: Path) -> bool:
    """macOS and Windows binaries must be able to import tkinter."""
    if sys.platform not in ("darwin", "win32"):
        return False
    name = binary.name.lower()
    if name.endswith(".pyz") or name.startswith("python"):
        return False
    return True


def _run_version(argv: list[str], env: dict, *, tk: bool = False) -> int:
    run_env = dict(env)
    if tk:
        run_env["AGENTCOOP_IMPORT_TK"] = "1"
    completed = subprocess.run(argv, env=run_env, capture_output=True, text=True)
    text = completed.stdout + completed.stderr
    if completed.returncode != 0 or (tk and "tkinter ok" not in completed.stdout):
        sys.stderr.write(text)
        return completed.returncode or 1
    return 0


def _watch_smoke(command: Path, env: dict, root: Path) -> int:
    """Prove watch returns when a message arrives during a 1 minute wait."""
    for tool in ("claude-code", "claude-desktop"):
        connected = subprocess.run(
            [str(command), "connect", tool, "--yes"],
            env=env,
            capture_output=True,
            text=True,
        )
        if connected.returncode != 0:
            sys.stderr.write(connected.stdout + connected.stderr)
            return connected.returncode or 1
    demo = root / "demo"
    demo.mkdir()
    added = subprocess.run(
        [
            str(command),
            "project",
            "add",
            str(demo),
            "--name",
            "smoke",
            "--driver",
            "cursor",
            "--helper",
            "claude_code=reviews",
            "--helper",
            "claude_desktop=thinks",
            "--gitignore",
            "no",
            "--yes",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        sys.stderr.write(added.stdout + added.stderr)
        return added.returncode or 1
    watch = subprocess.Popen(
        [str(command), "watch", "--as", "claude_code", "--project", "smoke", "--timeout", "1"],
        env=env,
        cwd=str(demo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(2)
    sent = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--to",
            "claude_code",
            "--subject",
            "Ping",
            "--body",
            "Hello from smoke.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if sent.returncode != 0:
        watch.kill()
        sys.stderr.write(sent.stdout + sent.stderr)
        return sent.returncode or 1
    try:
        output, errors = watch.communicate(timeout=40)
    except subprocess.TimeoutExpired:
        watch.kill()
        sys.stderr.write("watch did not finish\n")
        return 1
    if watch.returncode != 0 or "Handle these, then run agentcoop watch again." not in output:
        sys.stderr.write(output + errors)
        return watch.returncode or 1
    if "Read from cursor: Ping. Hello from smoke." not in output:
        sys.stderr.write(output + errors)
        return 1
    message_id = sent.stdout.strip().split()[0]
    helper_progress = subprocess.run(
        [
            str(command),
            "progress",
            "write",
            "--as",
            "claude_code",
            "--project",
            "smoke",
            "--for",
            message_id,
            "--percent",
            "40",
            "--step",
            "1 of 2",
            "--now",
            "Reading the smoke log.",
            "--body",
            "Done so far: the ping.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if helper_progress.returncode != 0 or "40" not in (helper_progress.stdout + helper_progress.stderr):
        sys.stderr.write(helper_progress.stdout + helper_progress.stderr)
        return helper_progress.returncode or 1
    driver_progress = subprocess.run(
        [
            str(command),
            "progress",
            "write",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--percent",
            "50",
            "--step",
            "1 of 2",
            "--now",
            "Running the smoke test.",
            "--body",
            "Working.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if driver_progress.returncode != 0 or "50" not in (driver_progress.stdout + driver_progress.stderr):
        sys.stderr.write(driver_progress.stdout + driver_progress.stderr)
        return driver_progress.returncode or 1
    for body in ("First answer.", "A later correction."):
        reply = subprocess.run(
            [
                str(command),
                "send",
                "--as",
                "claude_code",
                "--to",
                "cursor",
                "--project",
                "smoke",
                "--type",
                "answer",
                "--re",
                message_id,
                "--subject",
                "Re: Ping",
                "--body",
                body,
            ],
            env=env,
            cwd=str(demo),
            capture_output=True,
            text=True,
        )
        if reply.returncode != 0:
            sys.stderr.write(reply.stdout + reply.stderr)
            return reply.returncode or 1
    nudge = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "claude_code",
            "--to",
            "cursor",
            "--project",
            "smoke",
            "--type",
            "nudge",
            "--body",
            "The correction is ready.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if nudge.returncode != 0:
        sys.stderr.write(nudge.stdout + nudge.stderr)
        return nudge.returncode or 1
    checked = subprocess.run(
        [str(command), "check", "--as", "cursor", "--project", "smoke"],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    text = checked.stdout + checked.stderr
    if checked.returncode != 0 or "A later correction." not in text or "asks for your attention" not in text:
        sys.stderr.write(text)
        return checked.returncode or 1
    image = demo / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nsmoke")
    picture = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--to",
            "claude_code",
            "--subject",
            "Shot",
            "--body",
            "A picture.",
            "--attach",
            str(image),
            "--describe",
            "battle",
            "--kind",
            "screenshot",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if picture.returncode != 0 or "screenshot" not in (picture.stdout + picture.stderr):
        sys.stderr.write(picture.stdout + picture.stderr)
        return picture.returncode or 1
    note = demo / "from-helper.md"
    note.write_text("from the helper\n", encoding="utf-8")
    shared = subprocess.run(
        [
            str(command),
            "share",
            str(note),
            "--as",
            "claude_code",
            "--project",
            "smoke",
            "--describe",
            "notes",
            "--kind",
            "document",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    shared_path = shared.stdout.strip()
    if shared.returncode != 0 or not Path(shared_path).is_file():
        sys.stderr.write(shared.stdout + shared.stderr)
        return shared.returncode or 1
    back = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "claude_code",
            "--project",
            "smoke",
            "--to",
            "cursor",
            "--type",
            "answer",
            "--re",
            message_id,
            "--subject",
            "Re: Ping",
            "--body",
            "The file is attached.",
            "--attach",
            shared_path,
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if back.returncode != 0 or "from-helper.md" not in (back.stdout + back.stderr):
        sys.stderr.write(back.stdout + back.stderr)
        return back.returncode or 1
    board = Path(env["HOME"]) / ".agent_board"
    config_path = board / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["share_max_mb"] = 1
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    big = demo / "big.bin"
    with big.open("wb") as handle:
        handle.seek(1024 * 1024)
        handle.write(b"x")
    refused = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--to",
            "claude_code",
            "--subject",
            "Too big",
            "--body",
            "This should wait for the person.",
            "--attach",
            str(big),
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if refused.returncode == 0 or "Do not split it" not in (refused.stdout + refused.stderr):
        sys.stderr.write(refused.stdout + refused.stderr)
        return refused.returncode or 1
    if _helpers_smoke(command, env, demo, message_id) != 0:
        return 1
    return 0


def _helpers_smoke(command: Path, env: dict, demo: Path, message_id: str) -> int:
    """One grade, one ping with a heartbeat, one decline, and one second opinion."""
    answer = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "claude_code",
            "--to",
            "cursor",
            "--project",
            "smoke",
            "--type",
            "answer",
            "--re",
            message_id,
            "--subject",
            "Re: Ping",
            "--body",
            "Smoke answer to grade.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if answer.returncode != 0:
        sys.stderr.write(answer.stdout + answer.stderr)
        return answer.returncode or 1
    answer_id = [line.strip() for line in answer.stdout.splitlines() if line.strip()][-1]
    graded = subprocess.run(
        [
            str(command),
            "grade",
            answer_id,
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--score",
            "4",
            "--outcome",
            "used",
            "--note",
            "It helped.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if graded.returncode != 0:
        sys.stderr.write(graded.stdout + graded.stderr)
        return graded.returncode or 1
    ping = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--to",
            "claude_code",
            "--type",
            "ping",
            "--body",
            "Are you still there?",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if ping.returncode != 0:
        sys.stderr.write(ping.stdout + ping.stderr)
        return ping.returncode or 1
    ping_id = [line.strip() for line in ping.stdout.splitlines() if line.strip()][-1]
    beat = subprocess.run(
        [
            str(command),
            "heartbeat",
            "--as",
            "claude_code",
            "--project",
            "smoke",
            "--re",
            ping_id,
            "--status",
            "idle",
            "--next",
            "Here.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if beat.returncode != 0:
        sys.stderr.write(beat.stdout + beat.stderr)
        return beat.returncode or 1
    question = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--to",
            "claude_code",
            "--type",
            "question",
            "--needs-reply",
            "--subject",
            "Optional",
            "--body",
            "You can skip this.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if question.returncode != 0:
        sys.stderr.write(question.stdout + question.stderr)
        return question.returncode or 1
    question_id = [line.strip() for line in question.stdout.splitlines() if line.strip()][-1]
    declined = subprocess.run(
        [
            str(command),
            "decline",
            question_id,
            "--as",
            "claude_code",
            "--project",
            "smoke",
            "--reason",
            "Not my area.",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if declined.returncode != 0:
        sys.stderr.write(declined.stdout + declined.stderr)
        return declined.returncode or 1
    opinion = subprocess.run(
        [
            str(command),
            "send",
            "--as",
            "cursor",
            "--project",
            "smoke",
            "--to",
            "claude_code,claude_desktop",
            "--type",
            "question",
            "--subject",
            "Second look",
            "--body",
            "Which way?",
        ],
        env=env,
        cwd=str(demo),
        capture_output=True,
        text=True,
    )
    if opinion.returncode != 0 or opinion.stdout.count("\n") < 2:
        sys.stderr.write(opinion.stdout + opinion.stderr)
        return opinion.returncode or 1
    return 0


def _viewer_smoke(command: Path, env: dict) -> int:
    """Prove the installed build ships the viewer and serves its assets."""
    import re
    import urllib.error
    import urllib.request
    from http.cookiejar import CookieJar

    proc = subprocess.Popen(
        [str(command), "ui"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    url = ""
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line and proc.poll() is not None:
                break
            if "/?t=" in line:
                url = line.strip()
                break
        if not url:
            err = proc.stderr.read() if proc.stderr else ""
            sys.stderr.write("ui did not print a URL\n" + err)
            return 1
        jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(url, timeout=10) as response:
            html = response.read().decode("utf-8")
        if "The viewer app is missing" in html:
            sys.stderr.write("The viewer app is missing from this build.\n")
            return 1
        assets = re.findall(r'(?:src|href)="(\./assets/[^"]+)"', html)
        if len(assets) < 2:
            sys.stderr.write("index.html has no asset links\n")
            return 1
        base = url.split("/?t=")[0]
        token = url.split("?t=", 1)[1]
        for relative in assets:
            path = "/" + relative.lstrip("./")
            with opener.open(base + path, timeout=10) as asset:
                if asset.status != 200:
                    sys.stderr.write("asset failed: " + path + "\n")
                    return 1
                asset.read()
        try:
            urllib.request.urlopen(base + "/api/shutdown?t=" + token, timeout=10).close()
        except urllib.error.URLError:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _payload_from(starter: list[str]) -> Path | None:
    """Return the built file smoke was given, when it is a real file."""
    for item in starter:
        path = Path(item)
        if path.is_file() and path.suffix.lower() in (".pyz", ".exe", ".zip"):
            return path
        if path.is_file() and path.name.startswith("agentcoop"):
            return path
    return None


def _update_smoke(command: Path, starter: list[str], env: dict, root: Path) -> int:
    """Check the daily lookup, then install the built file that is already on disk."""
    checked = subprocess.run(
        [str(command), "update", "--check"],
        env=env,
        capture_output=True,
        text=True,
    )
    text = checked.stdout + checked.stderr
    if checked.returncode != 0:
        sys.stderr.write(text)
        return checked.returncode or 1
    payload = _payload_from(starter)
    if payload is None:
        return 0
    folder = root / "from-file"
    folder.mkdir()
    dest = folder / payload.name
    shutil.copy2(payload, dest)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    (folder / "SHA256SUMS").write_text(digest + "  " + dest.name + "\n", encoding="utf-8")
    installed = subprocess.run(
        [str(command), "update", "--from-file", str(dest)],
        env=env,
        capture_output=True,
        text=True,
    )
    if installed.returncode != 0:
        sys.stderr.write(installed.stdout + installed.stderr)
        return installed.returncode or 1
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    with tempfile.TemporaryDirectory(prefix="agentcoop-smoke-") as raw:
        home = Path(raw) / "home"
        home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["PYTHONPATH"] = str(src)
        starter = sys.argv[1:] or [sys.executable, "-m", "agentcoop"]
        if len(starter) == 1 and str(starter[0]).lower().endswith(".zip"):
            console, quiet = unpack_windows_zip(Path(starter[0]), Path(raw) / "windows")
            code = _run_version([str(quiet), "version"], env, tk=_wants_tk(quiet))
            if code != 0:
                return code
            starter = [str(console)]
        if len(starter) == 1:
            binary = Path(starter[0])
            if _wants_tk(binary):
                code = _run_version([str(binary), "version"], env, tk=True)
                if code != 0:
                    return code
            sibling = binary.with_name(_agentcoopw_name(binary.name))
            if sibling.is_file() and sibling.resolve() != binary.resolve():
                code = _run_version([str(sibling), "version"], env, tk=_wants_tk(sibling))
                if code != 0:
                    return code
        setup = subprocess.run(
            [
                *starter,
                "setup",
                "--connect",
                "cursor",
                "--schedule",
                "no",
                "--yes",
            ],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
        )
        if setup.returncode != 0:
            sys.stderr.write(setup.stdout + setup.stderr)
            return setup.returncode
        board = home / ".agent_board"
        bindir = board / "bin"
        if os.name == "nt":
            candidates = [bindir / "agentcoop.exe", bindir / "agentcoop.cmd"]
        else:
            candidates = [bindir / "agentcoop"]
        command = next((path for path in candidates if path.is_file()), candidates[0])
        argv = [str(command), "doctor"]
        if not command.is_file():
            sys.stderr.write("The installed command is missing: " + str(command) + "\n")
            return 1
        doctor = subprocess.run(argv, env=env, capture_output=True, text=True)
        text = doctor.stdout + doctor.stderr
        if doctor.returncode != 0:
            sys.stderr.write(text)
            return doctor.returncode
        for line in text.splitlines():
            if line.startswith("Fix:") and "command" in line.lower():
                sys.stderr.write(text)
                return 1
        sys.stdout.write(text)
        if _watch_smoke(command, env, Path(raw)) != 0:
            return 1
        if _viewer_smoke(command, env) != 0:
            return 1
        if _update_smoke(command, starter, env, Path(raw)) != 0:
            return 1
        prepare = subprocess.run(
            [*starter, "desktop", "prepare"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
        )
        if prepare.returncode != 0:
            sys.stderr.write(prepare.stdout + prepare.stderr)
            return prepare.returncode
        quiet_installed = bindir / "agentcoopw.exe"
        if quiet_installed.is_file():
            code = _run_version([str(quiet_installed), "version"], env, tk=_wants_tk(quiet_installed))
            if code != 0:
                return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
