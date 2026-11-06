"""Media download: ``GET /agents/{id}/rooms/{room_id}/media?mxc=`` (spike S5)."""

from __future__ import annotations

import httpx

MAX_BYTES = 10 * 1024 * 1024


class MediaError(RuntimeError):
    """The file could not be downloaded (the handler turns this into ``E_PDF_UNREADABLE``)."""


async def download(
    client: httpx.AsyncClient, base_url: str, agent_id: str, api_key: str, room_id: str, mxc: str
) -> bytes:
    """Return the bytes behind ``mxc`` (at most 10 MB). Raises MediaError on any failure."""
    url = f"{base_url.rstrip('/')}/agents/{agent_id}/rooms/{room_id}/media"
    try:
        resp = await client.get(
            url, params={"mxc": mxc}, headers={"Authorization": f"Bearer {api_key}"}
        )
    except httpx.HTTPError as exc:
        raise MediaError(type(exc).__name__) from exc
    if resp.status_code != 200:
        raise MediaError(f"status {resp.status_code}")
    if len(resp.content) > MAX_BYTES:
        raise MediaError("file too large")
    return resp.content
