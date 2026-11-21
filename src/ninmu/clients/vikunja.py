"""Vikunja REST client.

Talks to the Vikunja API (``/api/v1``) with a bearer token and exposes
only the handful of operations ninmu needs.
"""

from __future__ import annotations

from typing import Any

import httpx


class UpstreamError(Exception):
    """A Vikunja API call failed with a non-success status."""


class VikunjaClient:
    """Focused HTTP client for the Vikunja REST API."""

    def __init__(self, base_url: str, token: str, timeout: float = 30.0) -> None:
        """Create the client.

        Parameters
        ----------
        base_url : str
            Vikunja instance root, e.g. ``https://vikunja.example``.
        token : str
            Vikunja API token (Settings → API tokens).
        timeout : float
            Request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {token}"},
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
                f"Vikunja {method} {path} failed (HTTP {response.status_code}): {body}"
            )
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    async def list_projects(self) -> list[dict]:
        """List all projects the token can see.

        Returns
        -------
        list of dict
            Projects with at least ``id`` and ``title``.
        """
        return await self._request("GET", "/api/v1/projects")

    async def list_buckets(self, project_id: int) -> list[dict]:
        """List kanban buckets (the "statuses") for a project.

        Finds the project's kanban view, then returns its buckets with
        ``id``, ``title`` and task ``count``.
        """
        views = await self._request("GET", f"/api/v1/projects/{project_id}/views")
        kanban_view_id = self._pick_kanban_view(views)
        buckets = await self._request(
            "GET", f"/api/v1/projects/{project_id}/views/{kanban_view_id}/buckets"
        )
        return [
            {
                "id": bucket["id"],
                "title": bucket.get("title", "(untitled)"),
                "count": bucket.get("count", 0),
            }
            for bucket in buckets
        ]

    @staticmethod
    def _pick_kanban_view(views: list[dict]) -> int:
        """Pick the kanban view id, falling back to the first view."""
        for view in views:
            if view.get("view_kind") == "kanban":
                return view["id"]
        if not views:
            raise UpstreamError("Vikunja project has no views; cannot list buckets.")
        return views[0]["id"]

    async def create_task(
        self,
        project_id: int,
        title: str,
        description: str | None = None,
        due_date: str | None = None,
    ) -> dict:
        """Create a task in a project (Vikunja creates tasks via PUT).

        Returns
        -------
        dict
            The created task, at least ``id`` and ``title``.
        """
        payload: dict = {"title": title}
        if description:
            payload["description"] = description
        if due_date:
            payload["due_date"] = due_date
        return await self._request(
            "PUT", f"/api/v1/projects/{project_id}/tasks", json=payload
        )

    async def list_tasks(
        self,
        project_id: int,
        search: str | None = None,
        bucket_id: int | None = None,
        include_done: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """List tasks in a project, filtered client-side.

        Parameters
        ----------
        project_id : int
            Project to list tasks from.
        search : str, optional
            Case-insensitive substring match on title and description.
        bucket_id : int, optional
            Only tasks currently in this kanban bucket.
        include_done : bool
            Include completed tasks (default: hide them).
        limit : int
            Maximum number of tasks to return.
        """
        tasks = await self._request("GET", f"/api/v1/projects/{project_id}/tasks")
        matched: list[dict] = []
        needle = search.lower() if search else None
        for task in tasks:
            if not include_done and task.get("done"):
                continue
            if bucket_id is not None and task.get("bucket_id") != bucket_id:
                continue
            if needle:
                haystack = (
                    f"{task.get('title', '')} {task.get('description', '')}".lower()
                )
                if needle not in haystack:
                    continue
            matched.append(task)
            if len(matched) >= limit:
                break
        return matched

    async def set_bucket(self, task_id: int, bucket_id: int) -> dict:
        """Move a task to a kanban bucket (its "status")."""
        return await self._request(
            "POST", f"/api/v1/tasks/{task_id}", json={"bucket_id": bucket_id}
        )

    async def add_comment(self, task_id: int, comment: str) -> dict:
        """Add a comment to a task."""
        return await self._request(
            "PUT", f"/api/v1/tasks/{task_id}/comments", json={"comment": comment}
        )
