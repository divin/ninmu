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
    # Vikunja v2 caps the response page size at 50 and ignores larger
    # per_page values, so the client paginates in pages of 50.
    _MAX_PAGE_SIZE = 50
    # Hard stop for pagination so a misbehaving server cannot keep the
    # client fetching forever (20 pages = 1000 tasks).
    _MAX_PAGES = 20
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

    async def _get_all_pages(
        self, path: str, params: dict, page_size: int, max_items: int
    ) -> list[dict]:
        """Fetch a list endpoint across all its pages.

        Keeps fetching while the server sends back full pages, so a
        short page marks the end of the list. Stops early once
        ``max_items`` are collected. ``page_size`` is written into
        ``per_page`` and ``page`` counts from 1.
        """
        items: list[dict] = []
        for page in range(1, self._MAX_PAGES + 1):
            if len(items) >= max_items:
                return items
            response = await self._request(
                "GET",
                path,
                params={
                    **params,
                    "page": page,
                    "per_page": page_size,
                },
            )
            items.extend(response)
            if len(response) < page_size:
                return items
        return items

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
        page_size = min(limit, self._MAX_PAGE_SIZE)
        params: dict = {}
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
        tasks = await self._get_all_pages("/api/v1/tasks", params, page_size, limit)
        return tasks[:limit]

    async def list_tasks(
        self,
        project_id: int,
        search: str | None = None,
        include_done: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """List tasks in a project, filtered client-side.

        Note: v2 has no per-bucket task listing, so there is no bucket
        filter; a task's bucket is only visible via its kanban view.

        Parameters
        ----------
        project_id : int
            Project to list tasks from.
        search : str, optional
            Case-insensitive substring match on title and description.
        include_done : bool
            Include completed tasks (default: hide them).
        limit : int
            Maximum number of tasks to return.
        """
        page_size = min(limit, self._MAX_PAGE_SIZE)
        params: dict = {}
        if search:
            # v2 honours the server-side ``s`` filter, so searching does
            # not stop at the first page window; the client-side filter
            # below still trims any extra fields the server matches.
            params["s"] = search
        tasks = await self._get_all_pages(
            f"/api/v1/projects/{project_id}/tasks", params, page_size, limit
        )
        matched: list[dict] = []
        needle = search.lower() if search else None
        for task in tasks:
            if not include_done and task.get("done"):
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

    async def _replace_task(self, task_id: int, field: str, value: Any) -> dict:
        """Update one field of a task via v2's full-replacement update.

        Vikunja v2 treats ``POST /api/v1/tasks/{id}`` as a full
        replacement of the task, so partial updates would wipe the other
        fields. Fetch the task, change one field, post the whole object.

        Parameters
        ----------
        task_id : int
            Task to update.
        field : str
            Task field to change (e.g. ``"title"``).
        value : Any
            New value for the field.

        Returns
        -------
        dict
            The updated task.
        """
        task = await self.get_task(task_id)
        task[field] = value
        return await self._request("POST", f"/api/v1/tasks/{task_id}", json=task)

    async def update_task(
        self,
        task_id: int,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None = None,
        priority: int | None = None,
    ) -> dict:
        """Update a task's fields; only the provided ones change.

        Each provided field is applied via the v2 full-replacement
        update (see ``_replace_task``), so omitted fields keep their
        current value.
        """
        task = await self.get_task(task_id)
        changed = False
        for field, value in (
            ("title", title),
            ("description", description),
            ("due_date", due_date),
            ("priority", priority),
        ):
            if value is not None:
                task[field] = value
                changed = True
        if not changed:
            return task
        return await self._request("POST", f"/api/v1/tasks/{task_id}", json=task)

    async def set_task_done(self, task_id: int, done: bool) -> dict:
        """Mark a task done or reopen it."""
        return await self._replace_task(task_id, "done", done)

    async def set_bucket(self, task_id: int, bucket_id: int) -> dict:
        """Move a task to a kanban bucket (its "status").

        In Vikunja v2 bucket membership lives per view, so the client
        resolves the task's project and that project's kanban view,
        then posts the move to the view's bucket.
        """
        task = await self.get_task(task_id)
        project_id = task["project_id"]
        views = await self._request("GET", f"/api/v1/projects/{project_id}/views")
        view_id = self._pick_kanban_view(views)
        return await self._request(
            "POST",
            f"/api/v1/projects/{project_id}/views/{view_id}/buckets/{bucket_id}/tasks",
            json={
                "task_id": task_id,
                "project_view_id": view_id,
                "bucket_id": bucket_id,
            },
        )

    async def add_comment(self, task_id: int, comment: str) -> dict:
        """Add a comment to a task."""
        return await self._request(
            "PUT", f"/api/v1/tasks/{task_id}/comments", json={"comment": comment}
        )

    async def delete_task(self, task_id: int) -> dict:
        """Permanently delete a task."""
        return await self._request("DELETE", f"/api/v1/tasks/{task_id}")
