"""Orchestrator: startup self-checks, event dispatch and the run loop (architecture §5, §6).

One consumer handles one event at a time (``Runtime.lock``). Effects of a handled event and the
cursor commit together; the transport cursor is acknowledged only after that commit, so a crash
replays the event and the handlers are idempotent (approvals dedupe on message id).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator

from countersign import autopilot_flow
from countersign.chat import render
from countersign.config import Config
from countersign.core import commands
from countersign.core.canon import JsonValue
from countersign.core.codes import Code
from countersign.core.commands import CommandKind
from countersign.core.models import ProposalState
from countersign.decisions import handle_action, reply
from countersign.proposals import handle_proposal, refuse
from countersign.runtime import Runtime, StartupError
from countersign.store import db, receipts, repo
from countersign.switch import poll
from countersign.switch.models import ConnectionState, Gap, MessageEvent
from countersign.switch.ops import OpsClient, SwitchError, make_client
from countersign.switch.poster import Poster
from countersign.switch.ws import TakenOver, WsTransport

log = logging.getLogger("countersign.app")
SELF_NAME = "countersign"
SWEEP_EVERY_SEC = 30
IDLE_GUARD_SEC = 60
EXIT_TAKEN_OVER = 3
ACTIONS = (CommandKind.APPROVE, CommandKind.REJECT, CommandKind.CALLBACK)


class Reopen(Exception):
    """Rooms were located for the first time: reopen the socket with them claimed."""


# ---------------------------------------------------------------- dispatch


async def info_command(rt: Runtime, event: MessageEvent, cmd: commands.Command) -> None:
    """status / whoami / policy / help in ``#ap-approvals`` (read-only)."""
    assert rt.policy is not None
    p = event.payload
    row = repo.get_by_short_id(rt.conn, cmd.short_id) if cmd.short_id else None
    if cmd.kind is CommandKind.WHOAMI:
        text = render.whoami(p.sender_name, p.sender, p.sender_name in rt.policy.approvers)
    elif cmd.kind is CommandKind.HELP:
        text = render.help_text()
    elif cmd.kind is CommandKind.POLICY:
        tiers = [(t.below_cents, t.k) for t in rt.policy.tiers]
        text = render.policy_summary(
            rt.policy.approvers, rt.policy.proposers, rt.policy.window_sec, tiers, rt.policy_hash
        )
    elif cmd.short_id and row is None:
        text = render.not_counted(p.sender_name, Code.E_UNKNOWN_ID, id=cmd.short_id)
    else:
        text = _status_text(rt, row)
    await reply(rt, event, row, text)


def _status_text(rt: Runtime, row: repo.ProposalRow | None) -> str:
    rows = [row] if row is not None else repo.list_open(rt.conn)
    rules = autopilot_flow.active_rule_lines(rt) if row is None else []
    if not rows:
        return "\n".join(["No open proposals.", *rules])
    lines = []
    for r in rows:
        approvals = repo.approvals_for(rt.conn, r.action_hash)
        n = len({a.username for a in approvals if a.counted and a.decision.value == "approve"})
        cb = None
        if r.needs_callback:
            cb = any(a.counted and a.decision.value == "callback" for a in approvals)
        vendor = rt.vendors.get(r.vendor_id) if rt.vendors and r.vendor_id else None
        name = vendor.name if vendor else (r.vendor_id or "?")
        if r.state is ProposalState.OPEN:
            line = render.status_line(
                r.short_id, name, r.amount_cents or 0, n, r.k_required, cb, r.expires_at_ms
            )
        else:
            line = f"{r.short_id} · {name} · {r.state.value}"
        lines.append(line)
    return "\n".join([*lines, *rules])


async def dispatch(rt: Runtime, event: MessageEvent) -> None:
    """Route one addressed message by room and command."""
    assert rt.rooms is not None
    p = event.payload
    parsed = commands.parse(p.body)
    if event.room_id == rt.rooms.intake:
        if isinstance(parsed, commands.Command) and parsed.kind is CommandKind.PROPOSE:
            await handle_proposal(rt, event, parsed.arg or "")
        elif isinstance(parsed, commands.Command) and parsed.kind in ACTIONS:
            await refuse(rt, event, Code.E_WRONG_ROOM)
        else:
            await rt.say(rt.rooms.intake, render.bad_command(), p.thread_id or p.message_id)
    elif event.room_id == rt.rooms.approvals:
        if isinstance(parsed, commands.BadCommand):
            await reply(rt, event, None, render.bad_command())
        elif parsed.kind in ACTIONS:
            await handle_action(rt, event, parsed)
        elif parsed.kind is CommandKind.AUTOPILOT_LIST:
            await rt.guard(p.timestamp)
            await autopilot_flow.list_rules(rt, event)
        elif parsed.kind is CommandKind.AUTOPILOT_REVOKE:
            await rt.guard(p.timestamp)
            await autopilot_flow.manual_revoke(rt, event, parsed.arg or "")
        elif parsed.kind in (
            CommandKind.STATUS,
            CommandKind.WHOAMI,
            CommandKind.POLICY,
            CommandKind.HELP,
        ):
            await rt.guard(p.timestamp)
            await info_command(rt, event, parsed)
        else:
            await reply(rt, event, None, render.bad_command())


async def handle_event(rt: Runtime, transport: WsTransport | None, event: MessageEvent) -> None:
    """Dedupe, dispatch, then commit the dedupe marker and cursor together."""
    p = event.payload
    if p.sender_name == SELF_NAME or repo.was_processed(rt.conn, p.message_id):
        return
    async with rt.lock:
        try:
            await dispatch(rt, event)
        except Exception:
            # Fail closed: the handler's own transaction rolled back; log without bodies.
            log.exception("handler failed message=%s room=%s", p.message_id, event.room_id)
        with db.transaction(rt.conn):
            repo.mark_processed(rt.conn, p.message_id, event.room_id, p.timestamp, p.timestamp)
            if transport is not None:
                repo.meta_set(rt.conn, "cursor", str(event.sequence))
    if transport is not None:
        transport.ack(event.sequence)


# ---------------------------------------------------------------- periodic work


async def sweep_expired(rt: Runtime) -> int:
    """Expire OPEN proposals whose window closed (server time). Returns how many."""
    assert rt.rooms is not None
    async with rt.lock:
        now = await rt.server_now()
        done = 0
        for row in repo.expired_open(rt.conn, now):
            body: dict[str, JsonValue] = {"short_id": row.short_id, "code": str(Code.E_EXPIRED)}
            with db.transaction(rt.conn):
                repo.set_state(rt.conn, row.action_hash, ProposalState.EXPIRED)
                autopilot_flow.close_grant(rt, row, "grant expired")
                rcpt = receipts.append(rt.conn, "EXPIRED", row.action_hash, body, now)
            approvals = repo.approvals_for(rt.conn, row.action_hash)
            n = len({a.username for a in approvals if a.counted and a.decision.value == "approve"})
            text = render.expired(row.expires_at_ms, n, row.k_required, rcpt.seq)
            await rt.say(rt.rooms.approvals, text, row.card_message_id or None, row.short_id)
            await rt.post_receipt(rcpt, f"Expired with {n} of {row.k_required} approvals",
                                  "window closed")  # fmt: skip
            done += 1
        return done


async def periodic(rt: Runtime) -> None:
    """Expiry sweep every 30 s and an idle guard every 60 s."""
    ticks = 0
    while True:
        await asyncio.sleep(SWEEP_EVERY_SEC)
        ticks += 1
        try:
            await sweep_expired(rt)
            if ticks % (IDLE_GUARD_SEC // SWEEP_EVERY_SEC) == 0:
                async with rt.lock:
                    await rt.guard(await rt.server_now())
        except SwitchError as exc:
            log.warning("periodic task failed status=%s", exc.status)


# ---------------------------------------------------------------- startup and run


async def startup(rt: Runtime, transport: WsTransport) -> None:
    """Locate rooms, run the guard once, announce, and sweep what expired while offline."""
    rooms = await rt.locate_rooms()
    wanted = {rooms.intake, rooms.approvals, rooms.audit}
    if wanted - set(transport.rooms):
        repo_rooms = ",".join(sorted(wanted))
        with db.transaction(rt.conn):
            repo.meta_set(rt.conn, "rooms", repo_rooms)
        raise Reopen
    now = await rt.server_now()
    frozen = await rt.guard(now)
    assert rt.policy is not None
    text = render.startup(rt.policy_hash, len(rt.policy.approvers), rt.addressing_ok,
                          frozen is not None)  # fmt: skip
    await rt.say(rooms.approvals, text)
    await sweep_expired(rt)


async def recover_gap(rt: Runtime) -> None:
    """After a gap, replay recent addressed messages that were not handled."""
    assert rt.rooms is not None
    for room in (rt.rooms.intake, rt.rooms.approvals):
        try:
            msgs = await rt.ops.read_context(room, limit=50)
        except SwitchError:
            continue
        for seq, msg in enumerate(msgs):
            ev = poll.to_event(room, msg, seq)
            if ev and "@countersign" in ev.payload.body.lower():
                await handle_event(rt, None, ev)


async def _events(
    rt: Runtime, cfg: Config, transport: WsTransport
) -> AsyncIterator[MessageEvent | Gap | ConnectionState]:
    """Frames from the socket; in poll mode the socket only keeps ops alive."""
    if cfg.mode == "ws":
        async for frame in transport.events():
            yield frame
        return
    assert rt.rooms is not None
    queue: asyncio.Queue[ConnectionState | None] = asyncio.Queue()

    async def drain() -> None:
        async for frame in transport.events():
            if isinstance(frame, ConnectionState):
                queue.put_nowait(frame)

    task = asyncio.create_task(drain())
    try:
        first = await queue.get()
        if first is not None:
            yield first
        async for ev in poll.poll_events(
            rt.ops, [rt.rooms.intake, rt.rooms.approvals], SELF_NAME,
            repo.latest_event_ts(rt.conn), lambda m: not repo.was_processed(rt.conn, m),
        ):  # fmt: skip
            yield ev
    finally:
        task.cancel()


async def session(cfg: Config) -> None:
    """One connection lifetime. Raises Reopen, TakenOver or StartupError."""
    conn = db.open_db(cfg.db_path)
    cached = (repo.meta_get(conn, "rooms") or "").split(",")
    conn_id = repo.meta_get(conn, "connection_id")
    if conn_id is None:
        conn_id = str(uuid.uuid4())
        with db.transaction(conn):
            repo.meta_set(conn, "connection_id", conn_id)
    cursor = int(repo.meta_get(conn, "cursor") or 0)
    gen = repo.meta_get(conn, "generation")
    transport = WsTransport(
        cfg.switch_url, cfg.agent_id, cfg.api_key, conn_id, [r for r in cached if r],
        cursor=cursor, generation=int(gen) if gen else None,
    )  # fmt: skip
    async with make_client() as client:
        ops = OpsClient(cfg.switch_url, cfg.agent_id, cfg.api_key, lambda: conn_id, client)
        poster = Poster(ops)
        rt = Runtime(cfg, conn, ops, poster)
        tasks: list[asyncio.Task[None]] = [asyncio.create_task(poster.run())]
        started = False
        try:
            async for frame in _events(rt, cfg, transport):
                if isinstance(frame, ConnectionState):
                    with db.transaction(conn):
                        repo.meta_set(conn, "generation", str(frame.generation))
                    poster.invalidate()
                    if not started:
                        await startup(rt, transport)
                        tasks.append(asyncio.create_task(periodic(rt)))
                        started = True
                elif isinstance(frame, Gap):
                    log.warning("gap resumed_at=%s", frame.resumed_at)
                    if started:
                        await recover_gap(rt)
                elif started:
                    await handle_event(rt, transport if cfg.mode == "ws" else None, frame)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            conn.close()


async def run(cfg: Config) -> int:
    """Run until stopped. Exit codes: 0 clean, 1 startup failure, 3 taken over."""
    while True:
        try:
            await session(cfg)
            return 0
        except Reopen:
            continue
        except TakenOver:
            log.error("taken over by another connection; exiting")
            return EXIT_TAKEN_OVER
        except StartupError as exc:
            log.error("startup failed: %s", exc)
            return 1
