"""Tests for the MCP tool layer: terse formatting, limits, error wrapping.

Uses in-memory fake clients and a fastmcp in-memory Client to call the
tools the way the assistant would.
"""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from ninmu.clients.notediscovery import UpstreamError as NoteUpstreamError
from ninmu.config import Config
from ninmu.server import build_server


class FakeVikunja:
    """Records calls and returns canned data."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def list_projects(self):
        self.calls.append(("list_projects",))
        return [
            {"id": 1, "title": "Home"},
            {"id": 2, "title": "Work"},
        ]

    async def list_buckets(self, project_id: int):
        self.calls.append(("list_buckets", project_id))
        return [
            {"id": 5, "title": "Todo", "count": 2},
            {"id": 6, "title": "In Progress", "count": 0},
        ]

    async def create_task(
        self, project_id, title, description=None, due_date=None, bucket_id=None
    ):
        self.calls.append(
            ("create_task", project_id, title, description, due_date, bucket_id)
        )
        return {"id": 123, "title": title}

    async def search_tasks(
        self,
        query=None,
        filter_expr=None,
        limit=100,
        sort_by=None,
        include_done=False,
    ):
        self.calls.append(
            ("search_tasks", query, filter_expr, limit, sort_by, include_done)
        )
        return [
            {
                "id": 1,
                "title": "Fix garage door",
                "project_id": 14,
                "done": False,
            },
            {
                "id": 2,
                "title": "Garage sale prep",
                "project_id": 1,
                "done": False,
            },
        ]

    async def list_tasks(self, project_id, search=None, include_done=False, limit=100):
        self.calls.append(("list_tasks", project_id, search, include_done, limit))
        return [
            {"id": 1, "title": "Fix garage door", "done": False, "bucket_id": 6},
            {"id": 2, "title": "Buy milk", "done": False, "bucket_id": 5},
        ]

    async def set_bucket(self, task_id: int, bucket_id: int):
        self.calls.append(("set_bucket", task_id, bucket_id))
        return {"id": task_id, "bucket_id": bucket_id}

    async def add_comment(self, task_id: int, comment: str):
        self.calls.append(("add_comment", task_id, comment))
        return {"id": 9, "comment": comment}

    async def get_task(self, task_id: int):
        self.calls.append(("get_task", task_id))
        return {
            "id": task_id,
            "title": "Fix garage door",
            "description": "The left hinge is loose",
            "done": False,
            "due_date": "2026-04-01T00:00:00+00:00",
            "bucket_id": 6,
            "priority": 2,
        }

    async def update_task(
        self,
        task_id: int,
        title=None,
        description=None,
        due_date=None,
        priority=None,
    ):
        self.calls.append(
            ("update_task", task_id, title, description, due_date, priority)
        )
        return {"id": task_id}

    async def set_task_done(self, task_id: int, done: bool):
        self.calls.append(("set_task_done", task_id, done))
        return {"id": task_id, "done": done}

    async def delete_task(self, task_id: int):
        self.calls.append(("delete_task", task_id))
        return {"message": "ok"}


class FakeNoteDiscovery:
    """Records calls and returns canned data."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def list_folders(self):
        self.calls.append(("list_folders",))
        return [
            {"path": "Journal", "note_count": 3},
            {"path": "Projects", "note_count": 12},
        ]

    async def list_notes(self, folder=None, limit=None, offset=None):
        self.calls.append(("list_notes", folder, limit, offset))
        return {
            "notes": [
                {
                    "path": "Projects/alpha.md",
                    "title": "alpha",
                    "modified": "2026-03-17",
                },
                {"path": "Projects/beta.md", "title": "beta"},
            ]
        }

    async def create_note(self, folder, name, content, tags=None):
        self.calls.append(("create_note", folder, name, content, tags))
        return {"success": True, "path": "Projects/alpha.md"}

    async def append_note(self, path, content, add_timestamp=False):
        self.calls.append(("append_note", path, content, add_timestamp))
        return {"success": True}

    async def edit_note(self, path, old_str, new_str):
        self.calls.append(("edit_note", path, old_str, new_str))
        return {"success": True, "path": path}

    async def move_note(self, old_path, new_path):
        self.calls.append(("move_note", old_path, new_path))
        return {"success": True, "oldPath": old_path, "newPath": new_path}

    async def delete_note(self, path):
        self.calls.append(("delete_note", path))
        return {"success": True, "message": "Note deleted successfully"}

    async def get_note(self, path):
        self.calls.append(("get_note", path))
        return {
            "path": path,
            "content": "# Alpha\n\nBody text here.",
            "metadata": {},
        }

    async def search_notes(
        self, query, folder=None, limit=None, offset=None, recursive=True
    ):
        self.calls.append(("search_notes", query, folder, limit, offset, recursive))
        return {
            "results": [
                {
                    "name": "docker-setup",
                    "path": "Projects/docker-setup.md",
                    "folder": "Projects",
                    "matches": [
                        {
                            "line_number": 14,
                            "context": 'run <mark class="search-highlight">compose</mark> up',
                        },
                        {"line_number": 40, "context": "second hit"},
                    ],
                }
            ],
            "query": query,
            "pagination": {"total": 1, "limit": 20, "offset": 0, "has_more": False},
        }


@pytest.fixture
def config() -> Config:
    """A valid config for building the server with fake clients."""
    return Config(
        vikunja_url="https://vikunja.example",
        vikunja_token="vk-token",
        notediscovery_url="http://notes.example:8000",
        notediscovery_api_key="nd-key",
        mcp_auth_token=None,
        list_default_limit=20,
        list_max_limit=100,
    )


@pytest.fixture
def fakes():
    """Fresh fake clients for each test."""
    return FakeVikunja(), FakeNoteDiscovery()


@pytest.fixture
async def client(config, fakes):
    """An in-memory MCP client against the assembled server."""
    vikunja, notediscovery = fakes
    mcp = build_server(config, vikunja=vikunja, notediscovery=notediscovery)
    async with Client(mcp) as c:
        yield c


async def _call(client, tool_name, **kwargs):
    return await client.call_tool(tool_name, kwargs)


async def test_project_list_terse_output(client, fakes):
    result = await _call(client, "project_list")
    assert "Home" in result.content[0].text
    assert "#1" in result.content[0].text


async def test_bucket_list_lists_titles(client, fakes):
    result = await _call(client, "bucket_list", project_id=1)
    text = result.content[0].text
    assert "Todo" in text and "In Progress" in text


async def test_task_create_passes_description_and_due_date(client, fakes):
    result = await _call(
        client,
        "task_create",
        project_id=1,
        title="Fix garage door",
        description="Hinge",
        due_date="2026-04-01",
    )
    text = result.content[0].text
    assert "123" in text
    assert fakes[0].calls[-1] == (
        "create_task",
        1,
        "Fix garage door",
        "Hinge",
        "2026-04-01T00:00:00+00:00",
        None,
    )


async def test_task_create_rejects_bad_due_date(client):
    with pytest.raises(ToolError, match="due_date"):
        await _call(
            client, "task_create", project_id=1, title="X", due_date="not-a-date"
        )


async def test_task_list_terse_lines_and_notice(client, fakes):
    result = await _call(client, "task_list", project_id=1)
    text = result.content[0].text
    assert "#1 Fix garage door" in text
    assert "#2 Buy milk" in text


async def test_task_list_caps_limit_at_max(client, fakes):
    await _call(client, "task_list", project_id=1, limit=500)
    assert fakes[0].calls[-1][4] == 100


async def test_task_set_bucket_reports_move(client, fakes):
    result = await _call(client, "task_set_bucket", task_id=123, bucket_id=6)
    assert "123" in result.content[0].text
    assert fakes[0].calls[-1] == ("set_bucket", 123, 6)


async def test_task_comment_passes_text(client, fakes):
    result = await _call(client, "task_comment", task_id=123, comment="Started.")
    assert "Comment added" in result.content[0].text


async def test_folder_list_terse_output(client, fakes):
    result = await _call(client, "folder_list")
    text = result.content[0].text
    assert "Projects (12 notes)" in text
    assert "Journal (3 notes)" in text


async def test_note_list_passes_default_limit(client, fakes):
    await _call(client, "note_list", folder="Projects")
    call = fakes[1].calls[-1]
    assert call[1] == "Projects"
    assert call[2] == 20


async def test_note_create_builds_path_and_tags(client, fakes):
    result = await _call(
        client,
        "note_create",
        folder="Projects",
        name="alpha",
        content="# Alpha",
        tags=["python"],
    )
    text = result.content[0].text
    assert "Projects/alpha.md" in text
    assert fakes[1].calls[-1] == (
        "create_note",
        "Projects",
        "alpha",
        "# Alpha",
        ["python"],
    )


async def test_note_append_reports_target(client, fakes):
    result = await _call(client, "note_append", path="Journal/daily.md", content="Hi")
    assert "Journal/daily.md" in result.content[0].text


async def test_note_edit_passes_exact_strings(client, fakes):
    result = await _call(
        client, "note_edit", path="Projects/alpha.md", old_str="March", new_str="April"
    )
    assert fakes[1].calls[-1] == ("edit_note", "Projects/alpha.md", "March", "April")
    assert "Updated" in result.content[0].text


async def test_note_get_returns_content(client, fakes):
    result = await _call(client, "note_get", path="Projects/alpha.md")
    text = result.content[0].text
    assert "# Alpha" in text and "Body text here." in text
    assert fakes[1].calls[-1] == ("get_note", "Projects/alpha.md")


async def test_note_get_clips_very_long_content(config, fakes):
    class LongNote(FakeNoteDiscovery):
        async def get_note(self, path):
            return {"path": path, "content": "x" * 10_000, "metadata": {}}

    mcp = build_server(config, vikunja=fakes[0], notediscovery=LongNote())
    async with Client(mcp) as c:
        result = await _call(c, "note_get", path="big.md")
    text = result.content[0].text
    assert len(text) < 10_000
    assert "10,000" in text


async def test_note_search_formats_matches_and_strips_markup(client, fakes):
    result = await _call(client, "note_search", query="compose")
    text = result.content[0].text
    assert "Projects/docker-setup.md" in text
    assert "L14" in text and "L40" in text
    assert "compose up" in text
    assert "<mark>" not in text


async def test_note_search_passes_folder_and_default_limit(client, fakes):
    await _call(client, "note_search", query="compose", folder="Projects")
    assert fakes[1].calls[-1] == ("search_notes", "compose", "Projects", 20, None, True)


async def test_note_search_caps_limit_at_max(client, fakes):
    await _call(client, "note_search", query="compose", limit=500)
    assert fakes[1].calls[-1][3] == 100


async def test_note_move_reports_paths(client, fakes):
    result = await _call(client, "note_move", old_path="a.md", new_path="b/a.md")
    assert "a.md" in result.content[0].text and "b/a.md" in result.content[0].text


async def test_upstream_error_becomes_tool_error(config):
    fakes = FakeVikunja(), FakeNoteDiscovery()

    class Broken(FakeNoteDiscovery):
        async def list_folders(self):
            raise NoteUpstreamError(
                "NoteDiscovery GET /api/graph failed (HTTP 500): boom"
            )

    mcp = build_server(config, vikunja=fakes[0], notediscovery=Broken())
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="boom"):
            await c.call_tool("folder_list", {})


async def test_task_get_returns_terse_summary(client, fakes):
    result = await _call(client, "task_get", task_id=123)
    text = result.content[0].text
    assert "#123" in text
    assert "Fix garage door" in text
    assert "The left hinge is loose" in text
    assert "Due: 2026-04-01" in text
    assert "medium" in text
    assert "Bucket: #6" in text
    assert fakes[0].calls[-1] == ("get_task", 123)


async def test_task_update_passes_only_provided_fields(client, fakes):
    result = await _call(
        client, "task_update", task_id=123, title="Renamed", due_date="2026-05-01"
    )
    text = result.content[0].text
    assert "updated" in text
    assert fakes[0].calls[-1] == (
        "update_task",
        123,
        "Renamed",
        None,
        "2026-05-01T00:00:00+00:00",
        None,
    )


async def test_task_update_rejects_bad_due_date(client):
    with pytest.raises(ToolError, match="due_date"):
        await _call(client, "task_update", task_id=123, due_date="not-a-date")


async def test_task_set_done_marks_done_by_default(client, fakes):
    result = await _call(client, "task_set_done", task_id=123)
    assert "done" in result.content[0].text
    assert fakes[0].calls[-1] == ("set_task_done", 123, True)


async def test_task_set_done_can_reopen(client, fakes):
    result = await _call(client, "task_set_done", task_id=123, done=False)
    assert "reopened" in result.content[0].text
    assert fakes[0].calls[-1] == ("set_task_done", 123, False)


async def test_task_delete_reports_id(client, fakes):
    result = await _call(client, "task_delete", task_id=123)
    assert "123" in result.content[0].text
    assert fakes[0].calls[-1] == ("delete_task", 123)


async def test_note_delete_reports_path(client, fakes):
    result = await _call(client, "note_delete", path="Projects/alpha.md")
    assert "Projects/alpha.md" in result.content[0].text
    assert fakes[1].calls[-1] == ("delete_note", "Projects/alpha.md")


async def test_bucket_list_says_no_kanban_view_when_absent(config, fakes):
    from ninmu.clients.vikunja import NoKanbanView

    class NoKanban(FakeVikunja):
        async def list_buckets(self, project_id: int):
            self.calls.append(("list_buckets", project_id))
            raise NoKanbanView("Vikunja project has no kanban view.")

    mcp = build_server(config, vikunja=NoKanban(), notediscovery=fakes[1])
    async with Client(mcp) as c:
        result = await _call(c, "bucket_list", project_id=1)
    text = result.content[0].text
    assert "no kanban view" in text
    assert "Error:" not in text


async def test_task_list_shows_due_and_priority_and_skips_null_buckets(config, fakes):
    class Bucketless(FakeVikunja):
        async def list_tasks(
            self, project_id, search=None, include_done=False, limit=100
        ):
            self.calls.append(("list_tasks", project_id, search, include_done, limit))
            return [
                {
                    "id": 7,
                    "title": "Plane shelf",
                    "done": False,
                    "bucket_id": 0,
                    "due_date": "2026-08-01T00:00:00+00:00",
                    "priority": 3,
                },
                {"id": 8, "title": "No meta", "done": False, "bucket_id": None},
            ]

    mcp = build_server(config, vikunja=Bucketless(), notediscovery=fakes[1])
    async with Client(mcp) as c:
        result = await _call(c, "task_list", project_id=1)
    text = result.content[0].text
    assert "#7 Plane shelf" in text
    assert "due 2026-08-01" in text
    assert "high" in text
    assert "#8 No meta" in text
    assert "[bucket #" not in text


async def test_task_create_passes_bucket_id(client, fakes):
    result = await _call(
        client, "task_create", project_id=1, title="Bucketed", bucket_id=6
    )
    text = result.content[0].text
    assert "bucket #6" in text
    assert fakes[0].calls[-1] == (
        "create_task",
        1,
        "Bucketed",
        None,
        None,
        6,
    )


async def test_task_search_formats_lines_with_project(client, fakes):
    result = await _call(client, "task_search", query="garage")
    text = result.content[0].text
    assert "#1 Fix garage door (project #14)" in text
    assert "#2 Garage sale prep (project #1)" in text
    assert fakes[0].calls[-1] == (
        "search_tasks",
        "garage",
        None,
        20,
        None,
        False,
    )


async def test_task_search_empty(config, fakes):
    class NoHits(FakeVikunja):
        async def search_tasks(
            self,
            query=None,
            filter_expr=None,
            limit=100,
            sort_by=None,
            include_done=False,
        ):
            self.calls.append(
                ("search_tasks", query, filter_expr, limit, sort_by, include_done)
            )
            return []

    mcp = build_server(config, vikunja=NoHits(), notediscovery=fakes[1])
    async with Client(mcp) as c:
        result = await _call(c, "task_search", query="nothing")
    assert 'No tasks matching "nothing".' in result.content[0].text


async def test_task_search_caps_limit_at_max(client, fakes):
    await _call(client, "task_search", query="garage", limit=500)
    assert fakes[0].calls[-1][3] == 100


async def test_task_overview_buckets_by_due(config, fakes):
    from datetime import datetime, timedelta

    now = datetime.now().astimezone()
    overdue_date = (now - timedelta(days=2)).isoformat()
    # 23:59 today: still "today", but never already-overdue at test time.
    today_date = now.replace(hour=23, minute=59, second=0, microsecond=0).isoformat()
    upcoming_date = (now + timedelta(days=3)).isoformat()

    class DatedVikunja(FakeVikunja):
        async def search_tasks(
            self,
            query=None,
            filter_expr=None,
            limit=100,
            sort_by=None,
            include_done=False,
        ):
            self.calls.append(
                ("search_tasks", query, filter_expr, limit, sort_by, include_done)
            )
            return [
                {
                    "id": 41,
                    "title": "Overdue chore",
                    "project_id": 14,
                    "done": False,
                    "due_date": overdue_date,
                },
                {
                    "id": 42,
                    "title": "Today chore",
                    "project_id": 14,
                    "done": False,
                    "due_date": today_date,
                },
                {
                    "id": 43,
                    "title": "Later chore",
                    "project_id": 14,
                    "done": False,
                    "due_date": upcoming_date,
                },
            ]

    dated_vikunja = DatedVikunja()
    mcp = build_server(config, vikunja=dated_vikunja, notediscovery=fakes[1])
    async with Client(mcp) as c:
        result = await _call(c, "task_overview")
    text = result.content[0].text
    assert "Overdue:" in text
    assert "#41 Overdue chore (project #14) due" in text
    assert "Due today:" in text and "#42 Today chore" in text
    assert "Due within 7 days:" in text and "#43 Later chore" in text
    assert dated_vikunja.calls[-1] == (
        "search_tasks",
        None,
        "due_date < now+7d",
        100,
        "due_date",
        False,
    )


async def test_task_overview_all_clear(config, fakes):
    class EmptyVikunja(FakeVikunja):
        async def search_tasks(
            self,
            query=None,
            filter_expr=None,
            limit=100,
            sort_by=None,
            include_done=False,
        ):
            self.calls.append(
                ("search_tasks", query, filter_expr, limit, sort_by, include_done)
            )
            return []

    mcp = build_server(config, vikunja=EmptyVikunja(), notediscovery=fakes[1])
    async with Client(mcp) as c:
        result = await _call(c, "task_overview")
    assert (
        "No overdue tasks and nothing due in the next 7 days." in result.content[0].text
    )


async def test_task_overview_rejects_huge_window(client):
    with pytest.raises(ToolError, match="days"):
        await _call(client, "task_overview", days=400)


async def test_note_get_offset_paging(config, fakes):
    class LongNote(FakeNoteDiscovery):
        async def get_note(self, path):
            self.calls.append(("get_note", path))
            return {"path": path, "content": "y" * 7_000, "metadata": {}}

    mcp = build_server(config, vikunja=fakes[0], notediscovery=LongNote())
    async with Client(mcp) as c:
        first = await _call(c, "note_get", path="big.md")
        second = await _call(c, "note_get", path="big.md", offset=4000)
    first_text = first.content[0].text
    assert len(first_text) < 7_000
    assert "7,000" in first_text
    assert "offset=4000" in first_text
    second_text = second.content[0].text
    assert "clipped" not in second_text
    assert len(second_text) == 3_000
