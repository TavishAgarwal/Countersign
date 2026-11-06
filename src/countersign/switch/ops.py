"""Wrapper for ``POST /agents/{id}/ops/{op}`` with explicit timeouts and safe retries (CS-F2).

Spike findings: every call carries ``X-Switch-Connection-Id`` of an *open* websocket; document ids
come from the ``connect_to_room`` payload; ``read_context`` takes an explicit ``room_id``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable, Sequence
from email.utils import parsedate_to_datetime

import httpx

from countersign.core.addressing import Rule
from countersign.switch import media
from countersign.switch.models import (
    DocumentRef,
    RoomInfo,
    RoomSummary,
    SwitchParticipant,
)

TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_SAFE_TO_RETRY = {
    "read_context",
    "list_participants",
    "list_all_rooms",
    "get_agent_detail",
    "load_internal_documents",
    "connect_to_room",
}


class SwitchError(RuntimeError):
    """A Switch call failed. ``status`` is the HTTP status (0 for network errors)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"switch {status}: {detail}")
        self.status = status
        self.detail = detail


def make_client() -> httpx.AsyncClient:
    """One client per process, reused (rules.md §2)."""
    return httpx.AsyncClient(timeout=TIMEOUT)


class OpsClient:
    """Typed access to the ops Countersign uses."""

    def __init__(
        self,
        base_url: str,
        agent_id: str,
        api_key: str,
        connection_id: Callable[[], str],
        client: httpx.AsyncClient,
        sleep: Callable[[float], object] = asyncio.sleep,
    ) -> None:
        self.base_url, self.agent_id = base_url.rstrip("/"), agent_id
        self._key, self._conn, self._client, self._sleep = api_key, connection_id, client, sleep
        self.server_time_ms = 0  # newest Date header seen on a Switch response (server clock)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "X-Switch-Connection-Id": self._conn(),
        }

    async def call(self, op: str, **args: object) -> object:
        """Invoke an op and return its ``result``. Raises SwitchError on any failure."""
        url = f"{self.base_url}/agents/{self.agent_id}/ops/{op}"
        attempts = 3 if op in _SAFE_TO_RETRY else 1
        last: SwitchError | None = None
        for attempt in range(attempts):
            try:
                resp = await self._client.post(url, json=args, headers=self._headers())
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last = SwitchError(0, f"connect: {type(exc).__name__}")
            except httpx.HTTPError as exc:
                last = SwitchError(0, type(exc).__name__)
                if op not in _SAFE_TO_RETRY:
                    raise last from exc
            else:
                self._note_server_time(resp)
                if resp.status_code == 200:
                    return json.loads(resp.text).get("result")
                last = SwitchError(resp.status_code, resp.text[:200])
                if resp.status_code < 500 and resp.status_code != 409:
                    raise last
            if attempt + 1 < attempts:
                await self._sleep(1.0 * (attempt + 1))  # type: ignore[misc]
        assert last is not None
        raise last

    def _note_server_time(self, resp: httpx.Response) -> None:
        """Remember Switch's own clock from the ``Date`` header (second resolution)."""
        header = resp.headers.get("date")
        if header:
            with contextlib.suppress(TypeError, ValueError):
                ts = int(parsedate_to_datetime(header).timestamp() * 1000)
                self.server_time_ms = max(self.server_time_ms, ts)

    # ------------------------------------------------------------ typed helpers

    async def connect_to_room(self, room_id: str) -> RoomInfo:
        """Make ``room_id`` the connected room; returns participants and documents."""
        res = await self.call("connect_to_room", room_id=room_id)
        data = res if isinstance(res, dict) else {}
        return RoomInfo.model_validate({**data, "room_id": room_id})

    async def list_participants(self) -> tuple[SwitchParticipant, ...]:
        """Participants of the connected room."""
        res = await self.call("list_participants")
        items = res if isinstance(res, list) else []
        return tuple(SwitchParticipant.model_validate(i) for i in items)

    async def list_all_rooms(self) -> tuple[RoomSummary, ...]:
        """Every room the agent can see (rooms are matched by name, not hard-coded id)."""
        res = await self.call("list_all_rooms")
        items = res if isinstance(res, list) else []
        return tuple(RoomSummary.model_validate(i) for i in items)

    async def post_message(self, body: str, thread_id: str | None = None) -> str:
        """Post to the connected room (optionally in a thread). Returns the event id."""
        args: dict[str, object] = {"body": body}
        if thread_id:
            args["thread_id"] = thread_id
        res = await self.call("post_message", **args)
        return str(res.get("event_id", "")) if isinstance(res, dict) else ""

    async def post_message_http(self, room_id: str, content: str) -> None:
        """Fallback: a top-level post through ``POST /agents/{id}/message`` (no threads)."""
        url = f"{self.base_url}/agents/{self.agent_id}/message"
        try:
            resp = await self._client.post(
                url, json={"room_id": room_id, "content": content}, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise SwitchError(0, type(exc).__name__) from exc
        if resp.status_code != 200:
            raise SwitchError(resp.status_code, resp.text[:200])

    async def read_context(
        self, room_id: str, limit: int = 50, since: str | None = None
    ) -> list[dict[str, object]]:
        """Thread groups of a room, flattened to messages (root first, then replies)."""
        args: dict[str, object] = {"room_id": room_id, "limit": limit}
        if since:
            args["since"] = since
        res = await self.call("read_context", **args)
        threads = res.get("threads", []) if isinstance(res, dict) else []
        flat: list[dict[str, object]] = []
        for t in threads:
            root = t["root"]
            flat.append({**root, "thread_id": None})
            flat.extend({**r, "thread_id": root["id"]} for r in t.get("replies", []))
        return flat

    async def download_media(self, room_id: str, mxc: str) -> bytes:
        """Download an attachment (raises media.MediaError)."""
        return await media.download(
            self._client, self.base_url, self.agent_id, self._key, room_id, mxc
        )

    async def load_documents(self, ids: Sequence[str]) -> dict[str, str]:
        """Document id -> content for the connected room."""
        res = await self.call("load_internal_documents", ids=list(ids))
        items = res if isinstance(res, list) else []
        return {str(i["id"]): str(i.get("content", "")) for i in items}

    async def addressing_rules(self) -> tuple[Rule, ...]:
        """Countersign's own addressing rules as Switch holds them right now."""
        res = await self.call("get_agent_detail", agent_id=self.agent_id)
        policy = res.get("addressing_policy") if isinstance(res, dict) else None
        raw = policy.get("rules", []) if isinstance(policy, dict) else []
        return tuple(Rule.model_validate(r) for r in raw)


def documents_by_name(room: RoomInfo, names: Sequence[str]) -> dict[str, DocumentRef]:
    """Pick the documents with the given names from a connect payload."""
    return {d.name: d for d in room.documents if d.name in names}
