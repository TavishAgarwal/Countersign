"""``just accept-policy``: the owner's way out of a policy freeze (architecture.md §5.7).

Stop Countersign first: this opens the agent's own connection and would evict a running instance.
Shows what changed in policy.yaml / vendors.yaml since the ratified texts, asks for ``y``, then
records the new hash as ratified in one transaction with a POLICY_ACCEPTED_BY_OWNER receipt.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable

from countersign.core.canon import JsonValue
from countersign.core.policy import PolicyError, parse_documents
from countersign.runtime import Runtime
from countersign.store import db, receipts, repo
from countersign.switch.ops import SwitchError


def diff_lines(old: tuple[str, str], new: tuple[str, str]) -> list[str]:
    """A unified diff of both documents (empty when nothing changed)."""
    out: list[str] = []
    for name, a, b in (("policy.yaml", old[0], new[0]), ("vendors.yaml", old[1], new[1])):
        out += difflib.unified_diff(
            a.splitlines(), b.splitlines(), f"{name} (ratified)", f"{name} (now)", lineterm=""
        )
    return out


async def accept_policy(
    rt: Runtime, ask: Callable[[str], bool], say: Callable[[str], None] = print
) -> int:
    """Return 0 when the change was accepted, 1 when nothing was changed (and why)."""
    await rt.locate_rooms()
    try:
        texts = await rt.fetch_documents()
        _policy, _vendors, new_hash = parse_documents(*texts)
    except (PolicyError, SwitchError, RuntimeError) as exc:
        say(f"cannot read valid policy documents: {exc}")
        return 1
    old_hash = repo.meta_get(rt.conn, "ratified_policy_hash")
    if old_hash is None:
        say("no ratified policy yet: start Countersign once and it will ratify the first one")
        return 1
    if old_hash == new_hash:
        say(f"nothing to accept: the documents match the ratified hash {new_hash[:8]}…")
        return 1
    before = (
        repo.meta_get(rt.conn, "ratified_policy_text") or "",
        repo.meta_get(rt.conn, "ratified_vendors_text") or "",
    )
    say(f"ratified {old_hash[:8]}… -> now {new_hash[:8]}…")
    changes = diff_lines(before, texts) if any(before) else ["(ratified texts were not stored)"]
    for line in changes:
        say(line)
    if not ask("Accept these documents as the new policy? [y/N] "):
        say("not accepted; nothing changed")
        return 1
    ts = await rt.server_now()
    body: dict[str, JsonValue] = {"old_hash": old_hash, "new_hash": new_hash}
    with db.transaction(rt.conn):
        repo.meta_set(rt.conn, "ratified_policy_hash", new_hash)
        repo.meta_set(rt.conn, "ratified_policy_text", texts[0])
        repo.meta_set(rt.conn, "ratified_vendors_text", texts[1])
        rcpt = receipts.append(rt.conn, "POLICY_ACCEPTED_BY_OWNER", None, body, ts)
    rt.policy_hash = new_hash
    await rt.post_receipt(
        rcpt, "Policy accepted by the owner", f"{old_hash[:8]}… → {new_hash[:8]}…"
    )
    say("accepted: start Countersign again; payments resume once its checks pass")
    return 0
