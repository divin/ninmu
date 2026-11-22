"""FastMCP server assembly: 12 focused tools over two upstream APIs."""

from __future__ import annotations

import re
import textwrap
from datetime import datetime
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from ninmu.auth import StaticTokenVerifier
from ninmu.clients.notediscovery import NoteDiscoveryClient, UpstreamError
from ninmu.clients.vikunja import VikunjaClient
from ninmu.config import Config


async def _guard(awaitable):
    """Await an upstream call, turning UpstreamError into a ToolError."""
    try:
        return await awaitable
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
    return parsed.isoformat()


def build_server(
    config: Config,
    vikunja: Any | None = None,
    notediscovery: Any | None = None,
) -> FastMCP:
    """Build the FastMCP server with all 14 tools.

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
            `task_list`, `task_set_bucket`, `task_comment`.
            A task's "status" is its kanban bucket. Discover valid buckets
            with `bucket_list`, then move a task with `task_set_bucket`.

            NoteDiscovery (notes): `folder_list`, `note_list`, `note_get`,
            `note_search`, `note_create`, `note_append`, `note_edit`,
            `note_move`.
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
        buckets = await _guard(vikunja.list_buckets(project_id))
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
    ) -> str:
        due = _validate_due_date(due_date)
        task = await _guard(
            vikunja.create_task(
                project_id, title, description=description, due_date=due
            )
        )
        extras = [
            part
            for part in (description and "description set", due and f"due {due}")
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
        lines = [
            f"- #{t['id']} {t.get('title', '(untitled)')}"
            + (f" [bucket #{t['bucket_id']}]" if t.get("bucket_id") is not None else "")
            + (" (done)" if t.get("done") else "")
            for t in tasks
        ]
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
        name="note_get",
        description="Read a note's full markdown content by path. Very long notes are clipped.",
    )
    async def note_get(path: str) -> str:
        note = await _guard(notediscovery.get_note(path))
        content = note.get("content", "")
        if len(content) > NOTE_GET_MAX_CHARS:
            total = f"{len(content):,}"
            return (
                content[:NOTE_GET_MAX_CHARS]
                + f"\n\n... (clipped; note is {total} chars — use note_edit "
                "or note_append to work with the rest)"
            )
        return content

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
