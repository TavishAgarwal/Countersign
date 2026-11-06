"""Chat command grammar (design.md §9, CS-F9). Parsing only; no I/O."""

from __future__ import annotations

import re
from enum import StrEnum

from countersign.core.codes import Code
from countersign.core.models import Frozen

MENTION = "@countersign"
MAX_REASON_CHARS = 200
_ID_RE = re.compile(r"^(?:(CS|AP|PR)-)?([0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE)
_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL | re.IGNORECASE)
_TRAILING = ".,;:!?)`'\""
_LEADING = "`'\"("


class CommandKind(StrEnum):
    """Every command Countersign understands."""

    APPROVE = "approve"
    REJECT = "reject"
    CALLBACK = "callback"
    STATUS = "status"
    WHOAMI = "whoami"
    POLICY = "policy"
    HELP = "help"
    PROPOSE = "propose"
    AUTOPILOT_LIST = "autopilot_list"
    AUTOPILOT_REVOKE = "autopilot_revoke"


class Command(Frozen):
    """A parsed command. ``short_id`` is normalized (``CS-7f3a2c``); bare hex means ``CS-``."""

    kind: CommandKind
    short_id: str | None = None
    reason: str | None = None
    arg: str | None = None


class BadCommand(Frozen):
    """Not a known command. Shown as the help hint."""

    code: Code = Code.E_BAD_COMMAND


def normalize_id(token: str) -> str | None:
    """Return ``CS-7f3a2c`` style for ``cs-7f3a2c``, ``CS-7F3A2C`` or bare ``7f3a2c``."""
    cleaned = token.strip().lstrip(_LEADING).rstrip(_TRAILING)
    m = _ID_RE.match(cleaned)
    if not m:
        return None
    return f"{(m.group(1) or 'CS').upper()}-{m.group(2).lower()}"


def _after_mention(body: str) -> str:
    idx = body.lower().find(MENTION)
    return body[idx + len(MENTION) :] if idx >= 0 else body


def parse(body: str) -> Command | BadCommand:
    """Parse a message body into a Command, or BadCommand (``E_BAD_COMMAND``).

    Commands are case-insensitive. The text after the first ``@countersign`` is used, or the
    whole body when there is no mention (Switch already filtered to addressed messages).
    """
    text = _after_mention(body).strip()
    if not text:
        return BadCommand()
    head, _, rest = text.partition(" ")
    head, rest = head.strip().lower(), rest.strip()
    # "propose" may be followed directly by a newline and a fenced block
    if head.split("\n", 1)[0] == "propose":
        return Command(kind=CommandKind.PROPOSE, arg=text.split("propose", 1)[1].strip())
    if head in ("approve", "callback"):
        sid = normalize_id(rest.split()[0]) if rest else None
        if sid is None or len(rest.split()) != 1:
            return BadCommand()
        return Command(kind=CommandKind(head), short_id=sid)
    if head == "reject":
        parts = rest.split(None, 1)
        sid = normalize_id(parts[0]) if parts else None
        if sid is None:
            return BadCommand()
        reason = parts[1].strip()[:MAX_REASON_CHARS] if len(parts) > 1 else None
        return Command(kind=CommandKind.REJECT, short_id=sid, reason=reason or None)
    return _parse_simple(head, rest)


def _parse_simple(head: str, rest: str) -> Command | BadCommand:
    if head == "status":
        if not rest:
            return Command(kind=CommandKind.STATUS)
        sid = normalize_id(rest) if len(rest.split()) == 1 else None
        return Command(kind=CommandKind.STATUS, short_id=sid) if sid else BadCommand()
    if head in ("whoami", "policy", "help") and not rest:
        return Command(kind=CommandKind(head))
    if head == "autopilot":
        parts = rest.split()
        if parts == ["list"]:
            return Command(kind=CommandKind.AUTOPILOT_LIST)
        if len(parts) == 2 and parts[0].lower() == "revoke":
            return Command(kind=CommandKind.AUTOPILOT_REVOKE, arg=parts[1].upper())
    return BadCommand()


def extract_json_block(text: str) -> str | None:
    """Return the JSON inside exactly one fenced ```json block, else None (``E_BAD_PROPOSAL``).

    Zero blocks or more than one block fails closed.
    """
    blocks = _FENCE_RE.findall(text)
    return blocks[0].strip() if len(blocks) == 1 else None
