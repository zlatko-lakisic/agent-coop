"""Strict checks for the three board settings files."""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def format_problem(path: Path, problem: str) -> str:
    """Return the message loaders raise for a settings file."""
    return (
        str(path)
        + " is not in the expected format: "
        + problem
        + ". Run: agentcoop doctor"
    )


def check_projects(data: object) -> list[str]:
    """Return problems in projects.json. Empty means the file is usable."""
    if not isinstance(data, dict):
        return ["the file must be a JSON object"]
    problems = []
    if data.get("version") != 1:
        problems.append("version must be 1")
    projects = data.get("projects")
    if not isinstance(projects, dict):
        problems.append("projects must be an object")
        return problems
    for name, record in projects.items():
        if not isinstance(name, str) or not PROJECT_NAME_RE.fullmatch(name):
            problems.append("project name " + str(name) + " is not valid")
            continue
        if not isinstance(record, dict):
            problems.append(name + " must be an object")
            continue
        path = record.get("path")
        if not isinstance(path, str) or not Path(path).is_absolute():
            problems.append(name + " path must be an absolute path")
        driver = record.get("driver")
        if not isinstance(driver, str) or not AGENT_NAME_RE.fullmatch(driver or ""):
            problems.append(name + " driver must be an agent name")
        helpers = record.get("helpers")
        if not isinstance(helpers, dict):
            problems.append(name + " helpers must be an object")
        else:
            for helper, note in helpers.items():
                if not isinstance(helper, str) or not AGENT_NAME_RE.fullmatch(helper):
                    problems.append(name + " has a helper name that is not valid")
                if not isinstance(note, str):
                    problems.append(name + " helper notes must be text")
        if not isinstance(record.get("created"), str):
            problems.append(name + " created must be text")
        installed = record.get("installed")
        if not isinstance(installed, list):
            problems.append(name + " installed must be a list")
        else:
            for entry in installed:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    problems.append(name + " installed entries must include a path")
        history = record.get("roles_history")
        if history is not None:
            if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
                problems.append(name + " roles_history must be a list of objects")
    return problems


def check_agents(data: object) -> list[str]:
    """Return problems in agents.json. Empty means the file is usable."""
    if not isinstance(data, dict):
        return ["the file must be a JSON object"]
    problems = []
    if data.get("version") != 1:
        problems.append("version must be 1")
    agents = data.get("agents")
    if not isinstance(agents, dict):
        problems.append("agents must be an object")
        return problems
    for name, record in agents.items():
        if not isinstance(name, str) or not AGENT_NAME_RE.fullmatch(name):
            problems.append("agent name " + str(name) + " is not valid")
            continue
        if not isinstance(record, dict):
            problems.append(name + " must be an object")
            continue
        if not isinstance(record.get("tool"), str):
            problems.append(name + " tool must be text")
        if not isinstance(record.get("display"), str):
            problems.append(name + " display must be text")
        installed = record.get("installed")
        if installed is not None and not isinstance(installed, list):
            problems.append(name + " installed must be a list")
        runner = record.get("runner")
        if runner is not None:
            if not isinstance(runner, dict):
                problems.append(name + " runner must be an object")
            else:
                for key in ("api", "host", "model"):
                    if not isinstance(runner.get(key), str):
                        problems.append(name + " runner " + key + " must be text")
        model = record.get("model")
        if model is not None and not isinstance(model, str):
            problems.append(name + " model must be text")
        cli_path = record.get("cli_path")
        if cli_path is not None and not isinstance(cli_path, str):
            problems.append(name + " cli_path must be text")
    return problems


def check_config(data: object) -> list[str]:
    """Return problems in config.json. Empty means the file is usable."""
    if not isinstance(data, dict):
        return ["the file must be a JSON object"]
    problems = []
    if data.get("version") != 1:
        problems.append("version must be 1")
    roots = data.get("allowed_roots")
    if not isinstance(roots, list) or any(not isinstance(item, str) for item in roots):
        problems.append("allowed_roots must be a list of strings")
    limit = data.get("body_limit_bytes")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        problems.append("body_limit_bytes must be a positive integer")
    context_limit = data.get("context_limit_bytes")
    if context_limit is not None and (
        not isinstance(context_limit, int)
        or isinstance(context_limit, bool)
        or context_limit <= 0
    ):
        problems.append("context_limit_bytes must be a positive integer")
    for key in ("max_runs_per_day", "run_timeout_minutes", "progress_limit_bytes", "progress_every_minutes"):
        value = data.get(key)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
            problems.append(key + " must be a positive integer")
    auto_branch = data.get("auto_branch")
    if auto_branch is not None and not isinstance(auto_branch, str):
        problems.append("auto_branch must be text")
    launcher = data.get("launcher")
    if launcher is not None and not isinstance(launcher, str):
        problems.append("launcher must be text")
    path_changes = data.get("path_changes")
    if path_changes is not None:
        if not isinstance(path_changes, list) or any(
            not isinstance(item, str) for item in path_changes
        ):
            problems.append("path_changes must be a list of strings")
    return problems
