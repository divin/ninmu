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

    async def create_task(self, project_id, title, description=None, due_date=None):
        self.calls.append(("create_task", project_id, title, description, due_date))
        return {"id": 123, "title": title}

    async def list_tasks(
        self, project_id, search=None, bucket_id=None, include_done=False, limit=100
    ):
        self.calls.append(
            ("list_tasks", project_id, search, bucket_id, include_done, limit)
        )
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
        "2026-04-01T00:00:00",
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
    assert fakes[0].calls[-1][5] == 100


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
