"""Vikunja REST client.

Talks to the Vikunja API (``/api/v1``) with a bearer token and exposes
only the handful of operations ninmu needs.
"""

from __future__ import annotations

from typing import Any

import httpx


class UpstreamError(Exception):
    """A Vikunja API call failed with a non-success status."""


class NoKanbanView(UpstreamError):
    """The project exists but has no kanban view, so it has no buckets."""


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
        """Pick the kanban view id, or raise NoKanbanView if there is none."""
        for view in views:
            if view.get("view_kind") == "kanban":
                return view["id"]
        if not views:
            raise UpstreamError("Vikunja project has no views; cannot list buckets.")
        raise NoKanbanView("Vikunja project has no kanban view; cannot list buckets.")

    async def create_task(
        self,
        project_id: int,
        title: str,
        description: str | None = None,
        due_date: str | None = None,
        bucket_id: int | None = None,
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
        if bucket_id is not None:
            payload["bucket_id"] = bucket_id
        return await self._request(
            "PUT", f"/api/v1/projects/{project_id}/tasks", json=payload
        )

    async def search_tasks(
        self,
        query: str | None = None,
        filter_expr: str | None = None,
        limit: int = 100,
        sort_by: str | None = None,
        include_done: bool = False,
    ) -> list[dict]:
        """Search tasks across all projects (``GET /api/v1/tasks``).

        Parameters
        ----------
        query : str, optional
            Vikunja's server-side text search (title and description).
        filter_expr : str, optional
            A Vikunja filter expression, e.g. ``"due_date < now+7d"``
            (see vikunja.io/docs/filters).
        limit : int
            Requested page size; results are also capped client-side.
        sort_by : str, optional
            Field to sort by, e.g. ``"due_date"``.
        include_done : bool
            Include completed tasks (default: hide them via a filter).
        """
        params: dict = {"per_page": limit}
        if query:
            params["s"] = query
        if not include_done:
            done_filter = "done = false"
            filter_expr = (
                f"{done_filter} && {filter_expr}" if filter_expr else done_filter
            )
        if filter_expr:
            params["filter"] = filter_expr
        if sort_by:
            params["sort_by"] = sort_by
        tasks = await self._request("GET", "/api/v1/tasks", params=params)
        return tasks[:limit]

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

    async def get_task(self, task_id: int) -> dict:
        """Fetch one task by id."""
        return await self._request("GET", f"/api/v1/tasks/{task_id}")

    async def update_task(
        self,
        task_id: int,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None = None,
        priority: int | None = None,
    ) -> dict:
        """Update a task's fields; only the provided ones are sent.

        Vikunja treats ``PUT /api/v1/tasks/{id}`` as a partial update, so
        omitted fields keep their current value.
        """
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if due_date is not None:
            payload["due_date"] = due_date
        if priority is not None:
            payload["priority"] = priority
        return await self._request("PUT", f"/api/v1/tasks/{task_id}", json=payload)

    async def set_task_done(self, task_id: int, done: bool) -> dict:
        """Mark a task done or reopen it."""
        return await self._request(
            "PUT", f"/api/v1/tasks/{task_id}", json={"done": done}
        )

    async def set_bucket(self, task_id: int, bucket_id: int) -> dict:
        """Move a task to a kanban bucket (its "status").

        Vikunja has no ``POST /tasks/{id}`` route; partial updates go
        through ``PUT``.
        """
        return await self._request(
            "PUT", f"/api/v1/tasks/{task_id}", json={"bucket_id": bucket_id}
        )

    async def add_comment(self, task_id: int, comment: str) -> dict:
        """Add a comment to a task."""
        return await self._request(
            "PUT", f"/api/v1/tasks/{task_id}/comments", json={"comment": comment}
        )

    async def delete_task(self, task_id: int) -> dict:
        """Permanently delete a task."""
        return await self._request("DELETE", f"/api/v1/tasks/{task_id}")
