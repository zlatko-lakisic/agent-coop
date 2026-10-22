"""One-line summaries an agent can copy into its chat."""

from __future__ import annotations


def one_sentence(text: str) -> str:
    """Return the first sentence, as a single line."""
    flat = " ".join((text or "").split())
    if not flat:
        return ""
    end = len(flat)
    for index, char in enumerate(flat):
        if char not in ".!?":
            continue
        if index + 1 < len(flat) and not flat[index + 1].isspace():
            continue
        if index > 0 and flat[index - 1].isdigit():
            continue
        end = index + 1
        break
    sentence = flat[:end].strip()
    if sentence and sentence[-1] not in ".!?":
        sentence += "."
    return sentence


def _subject(subject: str) -> str:
    text = " ".join((subject or "").split()) or "Message"
    while text.endswith("."):
        text = text[:-1].rstrip()
    return text or "Message"


def read_line(sender: str, subject: str, body: str) -> str:
    """Return the line an agent posts after it reads a message."""
    sentence = one_sentence(body) or "No further detail."
    return "Read from " + sender + ": " + _subject(subject) + ". " + sentence


def sent_line(recipient: str, subject: str, body: str) -> str:
    """Return the line an agent posts after it writes to the board."""
    sentence = one_sentence(body) or "No further detail."
    return "Sent to " + recipient + ": " + _subject(subject) + ". " + sentence
