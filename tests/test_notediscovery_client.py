"""Tests for the NoteDiscovery REST client."""

from __future__ import annotations

import json

import pytest

from ninmu.clients.notediscovery import NoteDiscoveryClient

BASE_URL = "http://notes.example:8000"


@pytest.fixture
def client() -> NoteDiscoveryClient:
    """A client pointed at a fake instance with an API key."""
    return NoteDiscoveryClient(BASE_URL, api_key="notes-key")


def _assert_api_key(request) -> None:
    assert request.headers.get("X-API-Key") == "notes-key"


async def test_list_folders_derives_from_graph_nodes(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/graph",
        json={
            "nodes": [
                {"id": "Projects/alpha.md", "label": "alpha"},
                {"id": "Projects/beta.md", "label": "beta"},
                {"id": "Journal/daily.md", "label": "daily"},
                {"id": "standalone.md", "label": "standalone"},
            ],
            "edges": [],
        },
    )
    folders = await client.list_folders()

    _assert_api_key(httpx_mock.get_requests()[0])
    assert folders == [
        {"path": "Journal", "note_count": 1},
        {"path": "Projects", "note_count": 2},
    ]


async def test_list_notes_passes_pagination_and_folder(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/notes?folder=Projects&limit=5&offset=10",
        json={
            "notes": [
                {
                    "path": "Projects/alpha.md",
                    "title": "alpha",
                    "modified": "2026-03-17T10:00:00Z",
                },
            ]
        },
    )
    result = await client.list_notes(folder="Projects", limit=5, offset=10)

    assert result["notes"][0]["path"] == "Projects/alpha.md"


async def test_create_note_with_tags_prepends_frontmatter(httpx_mock, client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/api/notes/Projects/alpha.md",
        json={"success": True, "path": "Projects/alpha.md"},
    )
    result = await client.create_note(
        "Projects", "alpha", "# Alpha\nContent here", tags=["python", "tutorial"]
    )

    request = httpx_mock.get_requests()[0]
    _assert_api_key(request)
    body = json.loads(request.content)
    assert body["content"].startswith("---\ntags: [python, tutorial]\n---\n")
    assert "# Alpha" in body["content"]
    assert result["success"] is True


async def test_create_note_without_tags_has_no_frontmatter(httpx_mock, client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/api/notes/plain.md",
        json={"success": True},
    )
    await client.create_note(None, "plain", "Just text")

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["content"] == "Just text"


async def test_create_note_adds_md_extension(httpx_mock, client):
    httpx_mock.add_response(
        method="POST", url=f"{BASE_URL}/api/notes/Log/daily.md", json={"success": True}
    )
    await client.create_note("Log", "daily", "hi")
    request = httpx_mock.get_requests()[0]
    assert request.url.path.endswith("/api/notes/Log/daily.md")


async def test_append_note_patches_with_content(httpx_mock, client):
    httpx_mock.add_response(
        method="PATCH",
        url=f"{BASE_URL}/api/notes/Journal/daily.md",
        json={"success": True, "message": "Content appended successfully"},
    )
    result = await client.append_note(
        "Journal/daily.md", "New entry", add_timestamp=True
    )

    request = httpx_mock.get_requests()[0]
    assert request.method == "PATCH"
    _assert_api_key(request)
    assert json.loads(request.content) == {
        "content": "New entry",
        "add_timestamp": True,
    }
    assert result["success"] is True


async def test_edit_note_posts_replace(httpx_mock, client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/api/notes/replace",
        json={"success": True, "path": "Projects/alpha.md"},
    )
    result = await client.edit_note(
        "Projects/alpha.md", "Deadline: March", "Deadline: April"
    )

    request = httpx_mock.get_requests()[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/api/notes/replace")
    _assert_api_key(request)
    assert json.loads(request.content) == {
        "path": "Projects/alpha.md",
        "old_str": "Deadline: March",
        "new_str": "Deadline: April",
    }
    assert result["success"] is True


async def test_edit_note_conflict_surfaces_as_upstream_error(httpx_mock, client):
    from ninmu.clients.notediscovery import UpstreamError

    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/api/notes/replace",
        status_code=409,
        json={"detail": "old_str not found or not unique"},
    )
    with pytest.raises(UpstreamError, match="409"):
        await client.edit_note("Projects/alpha.md", "missing", "text")


async def test_move_note_posts_old_and_new_path(httpx_mock, client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/api/notes/move",
        json={"success": True, "oldPath": "draft.md", "newPath": "published/final.md"},
    )
    result = await client.move_note("draft.md", "published/final.md")

    request = httpx_mock.get_requests()[0]
    assert request.url.path.endswith("/api/notes/move")
    assert json.loads(request.content) == {
        "oldPath": "draft.md",
        "newPath": "published/final.md",
    }
    assert result["success"] is True
