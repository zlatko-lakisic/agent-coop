"""Errors the command can show to a person."""

from __future__ import annotations


class CoopError(Exception):
    """A problem the user can fix, with a process exit code."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class TransportDown(CoopError):
    """A message could not be carried right now. Nothing was wrong with the message.

    Raised only for a project that talks through GitHub. The caller keeps the
    message and tries again later. It does not reject it.
    """
