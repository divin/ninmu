"""NoteDiscovery REST client.

Talks to the NoteDiscovery API (``/api``) with an API key and exposes
only the handful of operations ninmu needs.
"""

from __future__ import annotations

import posixpath
from typing import Any

import httpx


class UpstreamError(Exception):
    """A NoteDiscovery API call failed with a non-success status."""


class NoteDiscoveryClient:
    """Focused HTTP client for the NoteDiscovery REST API."""

    def __init__(
        self, base_url: str, api_key: str | None, timeout: float = 30.0
    ) -> None:
        """Create the client.

        Parameters
        ----------
        base_url : str
            NoteDiscovery root, e.g. ``http://notes.example:8000``.
        api_key : str
            API key from NoteDiscovery's config (sent as ``X-API-Key``).
        timeout : float
            Request timeout in seconds.
        """
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key} if api_key else {},
            timeout=timeout,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        """Send a request and raise UpstreamError on failure."""
        response = await self._http.request(method, path, **kwargs)
        if response.status_code >= 400:
            body = response.text[:200]
            raise UpstreamError(
                f"NoteDiscovery {method} {path} failed (HTTP {response.status_code}): {body}"
            )
        return response.json()

    async def list_folders(self) -> list[dict]:
        """List folders with their note counts.

        NoteDiscovery's REST API has no dedicated folder-list endpoint, so
        folders are derived from the note graph. Empty folders therefore do
        not appear.
        """
        graph = await self._request("GET", "/api/graph")
        counts: dict[str, int] = {}
        for node in graph.get("nodes", []):
            parent = posixpath.dirname(node.get("id", ""))
            if parent:
                counts[parent] = counts.get(parent, 0) + 1
        return [
            {"path": path, "note_count": count}
            for path, count in sorted(counts.items())
        ]

    async def list_notes(
        self,
        folder: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        """List notes, optionally scoped to a folder, with pagination."""
        params: dict = {}
        if folder:
            params["folder"] = folder
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        return await self._request("GET", "/api/notes", params=params)

    async def create_note(
        self, folder: str | None, name: str, content: str, tags: list[str] | None = None
    ) -> dict:
        """Create a note from folder + name, optionally tagged.

        Tags are written as YAML frontmatter, which is how NoteDiscovery
        models tags. A missing ``.md`` suffix is added.
        """
        note_name = name if name.endswith(".md") else f"{name}.md"
        path = posixpath.join(folder, note_name) if folder else note_name
        if tags:
            content = f"---\ntags: [{', '.join(tags)}]\n---\n\n{content}"
        return await self._request(
            "POST", f"/api/notes/{path.lstrip('/')}", json={"content": content}
        )

    async def append_note(
        self, path: str, content: str, add_timestamp: bool = False
    ) -> dict:
        """Append content to an existing note."""
        return await self._request(
            "PATCH",
            f"/api/notes/{path.lstrip('/')}",
            json={"content": content, "add_timestamp": add_timestamp},
        )

    async def edit_note(self, path: str, old_str: str, new_str: str) -> dict:
        """Replace one exact occurrence of ``old_str`` in a note.

        NoteDiscovery refuses to write unless ``old_str`` appears exactly
        once (HTTP 409 otherwise).
        """
        return await self._request(
            "POST",
            "/api/notes/replace",
            json={"path": path, "old_str": old_str, "new_str": new_str},
        )

    async def move_note(self, old_path: str, new_path: str) -> dict:
        """Move or rename a note."""
        return await self._request(
            "POST", "/api/notes/move", json={"oldPath": old_path, "newPath": new_path}
        )
