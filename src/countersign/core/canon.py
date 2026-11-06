"""Canonical JSON, action hash and short ids (architecture.md §10, CS-F6)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Mapping

from countersign.core.models import Proposal

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

_HEX_STEPS = (6, 8)


def canonical_bytes(obj: Mapping[str, JsonValue] | list[JsonValue]) -> bytes:
    """Serialize with sorted keys, no whitespace, UTF-8 (the only way to hash an object)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sha256_hex(data: bytes) -> str:
    """Lowercase hex sha256."""
    return hashlib.sha256(data).hexdigest()


def canonical_proposal(proposal: Proposal, policy_hash: str) -> dict[str, JsonValue]:
    """The hashed object: validated fields minus advisory flags/note, plus the policy hash."""
    obj: dict[str, JsonValue] = proposal.model_dump(
        mode="json", by_alias=True, exclude={"intake_flags", "intake_note"}
    )
    obj["policy_hash"] = policy_hash
    return obj


def action_hash(proposal: Proposal, policy_hash: str) -> tuple[bytes, str]:
    """Return ``(canonical bytes, action hash)`` for a proposal under a policy hash."""
    data = canonical_bytes(canonical_proposal(proposal, policy_hash))
    return data, sha256_hex(data)


def short_id(hash_hex: str, taken: Collection[str] = (), prefix: str = "CS") -> str:
    """``CS-`` plus the first 6 hex of the hash, extended to 8 when another id has it.

    ``taken`` holds the short ids of *other* actions. Raises ValueError if even 8 collide,
    which the caller must treat as a refusal (fail closed).
    """
    for width in _HEX_STEPS:
        candidate = f"{prefix}-{hash_hex[:width]}"
        if candidate not in taken and not any(t.startswith(candidate) for t in taken):
            return candidate
    raise ValueError("short id collision at 8 hex characters")
