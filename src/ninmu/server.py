"""FastMCP server assembly: 21 focused tools over two upstream APIs."""

from __future__ import annotations

import re
import textwrap
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from ninmu.auth import StaticTokenVerifier
from ninmu.clients.notediscovery import NoteDiscoveryClient, UpstreamError
from ninmu.clients.vikunja import NoKanbanView, VikunjaClient
from ninmu.config import Config

# Vikunja priority numbers, for terse display in task lists.
_PRIORITY_NAMES = {1: "low", 2: "medium", 3: "high", 4: "urgent", 5: "do-now"}


async def _guard(awaitable):
    """Await an upstream call, turning UpstreamError into a ToolError.

    NoKanbanView is re-raised as-is so callers can respond with a
    friendlier message than a generic upstream failure.
    """
    try:
        return await awaitable
    except NoKanbanView:
        raise
    except UpstreamError as e:
        raise ToolError(str(e)) from e


# Upper bound for note_get output: a full note can dwarf a small local
# model's context, so long contents are clipped with a notice.
NOTE_GET_MAX_CHARS = 4000

# NoteDiscovery wraps search hits in this tag to highlight them in the UI.
_SEARCH_MARK_OPEN = '<mark class="search-highlight">'
_SEARCH_MARK_CLOSE = "</mark>"


def _clip(item_count: int, limit: int) -> str:
    """Format a 'more available' notice when results were truncated."""
    if item_count > limit:
        return (
            f" ... ({item_count} total; showing {limit} — narrow with a filter "
            "or pass a larger limit)"
        )
    return ""


def _validate_limit(limit: int, config: Config) -> int:
    """Clamp a requested page size to the configured ceiling."""
    if limit < 1:
        raise ToolError("limit must be a positive integer.")
    return min(limit, config.list_max_limit)


def _format_task_line(task: dict) -> str:
    """Format one task as a terse list line shared by all task list views."""
    line = f"- #{task['id']} {task.get('title', '(untitled)')}"
    if task.get("project_id"):
        line += f" (project #{task['project_id']})"
    # bucket_id 0/None means "no bucket", so only show real ones.
    if task.get("bucket_id"):
        line += f" [bucket #{task['bucket_id']}]"
    if task.get("due_date"):
        line += f" due {task['due_date'][:10]}"
    priority = task.get("priority")
    if priority:
        line += f" [{_PRIORITY_NAMES.get(priority, f'p{priority}')}]"
    if task.get("done"):
        line += " (done)"
    return line


def _validate_due_date(due_date: str | None) -> str | None:
    """Normalize an ISO date/datetime string for Vikunja."""
    if due_date is None:
        return None
    try:
        parsed = datetime.fromisoformat(due_date)
    except ValueError as e:
        raise ToolError(
            f"Invalid due_date: {due_date!r}. Use ISO format, e.g. 2026-04-01."
        ) from e
    if "T" not in due_date:
        parsed = parsed.replace(hour=0, minute=0)
    if parsed.tzinfo is None:
        # Vikunja rejects timezone-less datetimes (HTTP 400), so assume UTC.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def build_server(
    config: Config,
    vikunja: Any | None = None,
    notediscovery: Any | None = None,
) -> FastMCP:
    """Build the FastMCP server with all 21 tools.

    Parameters
    ----------
    config : Config
        Loaded configuration (URLs, tokens, limits).
    vikunja : VikunjaClient, optional
        Injected client (used by tests); built from config when omitted.
    notediscovery : NoteDiscoveryClient, optional
        Injected client (used by tests); built from config when omitted.

    Returns
    -------
    FastMCP
        The assembled server, ready to be served over streamable HTTP.
    """
    config.validate()
    if vikunja is None:
        vikunja = VikunjaClient(config.vikunja_url, config.vikunja_token or "")
    if notediscovery is None:
        notediscovery = NoteDiscoveryClient(
            config.notediscovery_url, config.notediscovery_api_key
        )

    # Optional bearer auth: enforced only when NINMU_AUTH_TOKEN is set.
    # Unset means an open server, which is acceptable on a trusted LAN.
    auth = StaticTokenVerifier(config.mcp_auth_token) if config.mcp_auth_token else None

    mcp = FastMCP(
        "ninmu",
        auth=auth,
        instructions=textwrap.dedent("""\
            Focused tools for two homelab services:

            Vikunja (tasks): `project_list`, `bucket_list`, `task_create`,
            `task_list`, `task_get`, `task_update`, `task_set_bucket`,
            `task_set_done`, `task_comment`, `task_delete`, `task_search`,
            `task_overview`.
            A task's "status" is its kanban bucket. Discover valid buckets
            with `bucket_list`, then move a task with `task_set_bucket`.

            NoteDiscovery (notes): `folder_list`, `note_list`, `note_get`,
            `note_search`, `note_create`, `note_append`, `note_edit`,
            `note_move`, `note_delete`.
            `note_list` and `note_search` are paginated; use `folder` to
            scope them and keep `limit` small — responses are terse to save
            context space.
            """),
    )

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "service": "ninmu"})

    # --- Vikunja tools ---------------------------------------------------

    @mcp.tool(
        name="project_list",
        description="List Vikunja projects (id + title). Call this first to resolve a project id.",
    )
    async def project_list() -> str:
        projects = await _guard(vikunja.list_projects())
        if not projects:
            return "No projects found."
        lines = [f"- #{p['id']} {p.get('title', '(untitled)')}" for p in projects]
        return "Vikunja projects:\n" + "\n".join(lines)

    @mcp.tool(
        name="bucket_list",
        description="List the kanban buckets (valid task statuses) of a project, with ids.",
    )
    async def bucket_list(project_id: int) -> str:
        try:
            buckets = await _guard(vikunja.list_buckets(project_id))
        except NoKanbanView:
            return (
                f"Project {project_id} has no kanban view, so there are "
                "no buckets to list."
            )
        lines = [f"- #{b['id']} {b['title']} ({b['count']} tasks)" for b in buckets]
        return f"Buckets for project {project_id}:\n" + "\n".join(lines)

    @mcp.tool(
        name="task_create",
        description="Create a task in a Vikunja project. Returns the new task id.",
    )
    async def task_create(
        project_id: int,
        title: str,
        description: str | None = None,
        due_date: str | None = None,
        bucket_id: int | None = None,
    ) -> str:
        due = _validate_due_date(due_date)
        task = await _guard(
            vikunja.create_task(
                project_id,
                title,
                description=description,
                due_date=due,
                bucket_id=bucket_id,
            )
        )
        extras = [
            part
            for part in (
                description and "description set",
                due and f"due {due}",
                bucket_id is not None and f"bucket #{bucket_id}",
            )
            if part
        ]
        suffix = f" ({'; '.join(extras)})" if extras else ""
        return f'Created task #{task["id"]} "{title}" in project {project_id}{suffix}.'

    @mcp.tool(
        name="task_list",
        description=(
            "List tasks in a project to find the one you want to update. "
            "Optional: search text, bucket_id filter, include_done (default hides done tasks), limit."
        ),
    )
    async def task_list(
        project_id: int,
        search: str | None = None,
        bucket_id: int | None = None,
        include_done: bool = False,
        limit: int | None = None,
    ) -> str:
        limit = _validate_limit(
            limit if limit is not None else config.list_default_limit, config
        )
        tasks = await _guard(
            vikunja.list_tasks(
                project_id,
                search=search,
                bucket_id=bucket_id,
                include_done=include_done,
                limit=limit,
            )
        )
        if not tasks:
            return "No matching tasks."
        lines = [_format_task_line(t) for t in tasks]
        notice = (
            "\n(limit reached — refine with search/bucket_id, or pass a larger limit)"
            if len(tasks) >= limit
            else ""
        )
        return "Tasks:\n" + "\n".join(lines) + notice

    @mcp.tool(
        name="task_set_bucket",
        description="Change a task's status by moving it to a kanban bucket (e.g. todo → in progress).",
    )
    async def task_set_bucket(task_id: int, bucket_id: int) -> str:
        await _guard(vikunja.set_bucket(task_id, bucket_id))
        return f"Task #{task_id} moved to bucket #{bucket_id}."

    @mcp.tool(
        name="task_comment",
        description="Add a comment to a Vikunja task.",
    )
    async def task_comment(task_id: int, comment: str) -> str:
        await _guard(vikunja.add_comment(task_id, comment))
        return f"Comment added to task #{task_id}."

    @mcp.tool(
        name="task_search",
        description=(
            "Search tasks across ALL projects by text (title/description). "
            "Use when you know what the task says but not which project holds it. "
            "Optional: include_done, limit."
        ),
    )
    async def task_search(
        query: str, include_done: bool = False, limit: int | None = None
    ) -> str:
        limit = _validate_limit(
            limit if limit is not None else config.list_default_limit, config
        )
        tasks = await _guard(
            vikunja.search_tasks(query, limit=limit, include_done=include_done)
        )
        if not tasks:
            return f'No tasks matching "{query}".'
        lines = [_format_task_line(t) for t in tasks]
        notice = (
            "\n(limit reached — refine the query, or pass a larger limit)"
            if len(tasks) >= limit
            else ""
        )
        return f'Tasks matching "{query}":\n' + "\n".join(lines) + notice

    @mcp.tool(
        name="task_overview",
        description=(
            "Show open tasks that are overdue, due today, or due within the "
            "next days (default 7) across ALL projects."
        ),
    )
    async def task_overview(days: int = 7) -> str:
        if not 1 <= days <= 30:
            raise ToolError("days must be between 1 and 30.")
        tasks = await _guard(
            vikunja.search_tasks(
                filter_expr=f"due_date < now+{days}d",
                limit=100,
                sort_by="due_date",
            )
        )
        if not tasks:
            return f"No overdue tasks and nothing due in the next {days} days."

        now = datetime.now().astimezone()
        buckets: dict[str, list[str]] = {"overdue": [], "today": [], "upcoming": []}
        for t in tasks:
            try:
                due = datetime.fromisoformat(t["due_date"])
            except (KeyError, ValueError):
                # A task without a usable due_date cannot be placed in a
                # time bucket; the server-side filter should exclude it.
                continue
            if due < now:
                buckets["overdue"].append(_format_task_line(t))
            elif due.date() == now.date():
                buckets["today"].append(_format_task_line(t))
            else:
                buckets["upcoming"].append(_format_task_line(t))

        sections = [
            (title, lines)
            for title, lines in (
                ("Overdue:", buckets["overdue"]),
                ("Due today:", buckets["today"]),
                (f"Due within {days} days:", buckets["upcoming"]),
            )
            if lines
        ]
        return "Task overview:\n" + "\n".join(
            title + "\n" + "\n".join(lines) for title, lines in sections
        )

    @mcp.tool(
        name="task_get",
        description="Fetch one Vikunja task by id: title, description, due date, bucket, priority, done.",
    )
    async def task_get(task_id: int) -> str:
        task = await _guard(vikunja.get_task(task_id))
        lines = [f"Task #{task['id']}: {task.get('title', '(untitled)')}"]
        if task.get("done"):
            lines.append("Status: done")
        if task.get("due_date"):
            lines.append(f"Due: {task['due_date'][:10]}")
        if task.get("bucket_id"):
            lines.append(f"Bucket: #{task['bucket_id']}")
        priority = task.get("priority")
        if priority:
            lines.append(f"Priority: {_PRIORITY_NAMES.get(priority, f'p{priority}')}")
        description = task.get("description")
        if description:
            lines.extend(["", description])
        return "\n".join(lines)

    @mcp.tool(
        name="task_update",
        description=(
            "Update a Vikunja task. Only provided fields change. "
            "priority: 1 low, 2 medium, 3 high, 4 urgent, 5 do-now. "
            "due_date: ISO date, e.g. 2026-04-01."
        ),
    )
    async def task_update(
        task_id: int,
        title: str | None = None,
        description: str | None = None,
        due_date: str | None = None,
        priority: int | None = None,
    ) -> str:
        due = _validate_due_date(due_date)
        await _guard(
            vikunja.update_task(
                task_id,
                title=title,
                description=description,
                due_date=due,
                priority=priority,
            )
        )
        changed = [
            name
            for name, value in (
                ("title", title),
                ("description", description),
                ("due_date", due),
                ("priority", priority),
            )
            if value is not None
        ]
        return f"Task #{task_id} updated ({', '.join(changed)})."

    @mcp.tool(
        name="task_set_done",
        description="Mark a Vikunja task done (default) or reopen it with done=false.",
    )
    async def task_set_done(task_id: int, done: bool = True) -> str:
        await _guard(vikunja.set_task_done(task_id, done))
        state = "done" if done else "reopened"
        return f"Task #{task_id} marked {state}."

    @mcp.tool(
        name="task_delete",
        description="Permanently delete a Vikunja task by id. There is no undo.",
    )
    async def task_delete(task_id: int) -> str:
        await _guard(vikunja.delete_task(task_id))
        return f"Task #{task_id} deleted."

    # --- NoteDiscovery tools --------------------------------------------

    @mcp.tool(
        name="folder_list",
        description="List note folders with their note counts. Empty folders are not shown.",
    )
    async def folder_list() -> str:
        folders = await _guard(notediscovery.list_folders())
        if not folders:
            return "No folders found (or the vault is empty)."
        lines = [f"- {f['path']} ({f['note_count']} notes)" for f in folders]
        return "Folders:\n" + "\n".join(lines)

    @mcp.tool(
        name="note_list",
        description="List notes in a folder. Paginated: limit (default 20) and offset.",
    )
    async def note_list(
        folder: str | None = None, limit: int | None = None, offset: int = 0
    ) -> str:
        limit = _validate_limit(
            limit if limit is not None else config.list_default_limit, config
        )
        result = await _guard(
            notediscovery.list_notes(folder=folder, limit=limit, offset=offset)
        )
        notes = result.get("notes", [])
        if not notes:
            return "No notes found."
        lines = [
            f"- {n['path']}" + (f" — {n['title']}" if n.get("title") else "")
            for n in notes
        ]
        total = result.get("total")
        notice = _clip(total, limit) if isinstance(total, int) else ""
        return "Notes:\n" + "\n".join(lines) + notice

    @mcp.tool(
        name="note_create",
        description="Create a note from folder + name (+ content). Optional tags become YAML frontmatter.",
    )
    async def note_create(
        folder: str | None, name: str, content: str, tags: list[str] | None = None
    ) -> str:
        result = await _guard(
            notediscovery.create_note(folder, name, content, tags=tags)
        )
        path = result.get("path", f"{folder}/{name}" if folder else name)
        tag_note = f" ({len(tags)} tags)" if tags else ""
        return f"Created note {path}{tag_note}."

    @mcp.tool(
        name="note_append",
        description="Append content to an existing note (journals, logs). Optional timestamp header.",
    )
    async def note_append(path: str, content: str, add_timestamp: bool = False) -> str:
        await _guard(
            notediscovery.append_note(path, content, add_timestamp=add_timestamp)
        )
        return f"Appended to note {path}."

    @mcp.tool(
        name="note_edit",
        description=(
            "Replace one exact occurrence of old_str in a note with new_str. "
            "old_str must appear exactly once (copy it from note content)."
        ),
    )
    async def note_edit(path: str, old_str: str, new_str: str) -> str:
        result = await _guard(notediscovery.edit_note(path, old_str, new_str))
        return f"Updated note {result.get('path', path)}."

    @mcp.tool(
        name="note_move",
        description="Move or rename a note to a new path.",
    )
    async def note_move(old_path: str, new_path: str) -> str:
        await _guard(notediscovery.move_note(old_path, new_path))
        return f"Moved note {old_path} → {new_path}."

    @mcp.tool(
        name="note_delete",
        description="Permanently delete a note by path. There is no undo.",
    )
    async def note_delete(path: str) -> str:
        await _guard(notediscovery.delete_note(path))
        return f"Deleted note {path}."

    @mcp.tool(
        name="note_get",
        description="Read a note's full markdown content by path. Very long notes are clipped.",
    )
    async def note_get(path: str, offset: int = 0) -> str:
        note = await _guard(notediscovery.get_note(path))
        content = note.get("content", "")
        start = max(offset, 0)
        if start > 0 and start >= len(content):
            return (
                f"Offset {start} is past the end of the note ({len(content):,} chars)."
            )
        window = content[start : start + NOTE_GET_MAX_CHARS]
        if start + len(window) < len(content):
            next_offset = start + NOTE_GET_MAX_CHARS
            total = f"{len(content):,}"
            return (
                window + f"\n\n[...clipped — {total} chars total, showing "
                f"{start}:{next_offset}; pass offset={next_offset} to continue]"
            )
        return window

    @mcp.tool(
        name="note_search",
        description=(
            "Full-text search across note contents. Returns paths with line "
            "numbers and context snippets. Optional: folder filter and pagination."
        ),
    )
    async def note_search(
        query: str,
        folder: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> str:
        limit = _validate_limit(
            limit if limit is not None else config.list_default_limit, config
        )
        result = await _guard(
            notediscovery.search_notes(query, folder=folder, limit=limit, offset=offset)
        )
        results = result.get("results", [])
        if not results:
            return f'No notes matching "{query}".'
        lines = []
        for hit in results:
            for match in hit.get("matches", []):
                context = match.get("context", "")
                context = context.replace(_SEARCH_MARK_OPEN, "").replace(
                    _SEARCH_MARK_CLOSE, ""
                )
                context = re.sub(r"\s+", " ", context).strip()
                lines.append(
                    f"- {hit['path']} L{match.get('line_number', '?')}: {context}"
                )
        header = f'Search "{query}":'
        pagination = result.get("pagination")
        total = pagination.get("total") if isinstance(pagination, dict) else None
        notice = _clip(total, limit) if isinstance(total, int) else ""
        return header + "\n" + "\n".join(lines) + notice

    return mcp
