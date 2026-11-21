"""Tests for the Vikunja REST client.

Every test pins the exact request ninmu sends (method, path, JSON body)
and the terse value the client returns, so upstream API changes surface
as test failures instead of silent breakage.
"""

from __future__ import annotations

import json

import pytest

from ninmu.clients.vikunja import VikunjaClient

BASE_URL = "https://vikunja.example"


@pytest.fixture
def client() -> VikunjaClient:
    """A client pointed at a fake instance."""
    return VikunjaClient(BASE_URL, token="secret-token")


def _assert_bearer_header(request) -> None:
    assert request.headers["Authorization"] == "Bearer secret-token"


async def test_list_projects_returns_id_and_title(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects",
        json=[{"id": 1, "title": "Home"}, {"id": 2, "title": "Work"}],
    )
    projects = await client.list_projects()

    _assert_bearer_header(httpx_mock.get_requests()[0])
    assert projects == [
        {"id": 1, "title": "Home"},
        {"id": 2, "title": "Work"},
    ]


async def test_list_buckets_uses_kanban_view(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects/1/views",
        json=[
            {"id": 11, "view_kind": "list"},
            {"id": 12, "view_kind": "kanban"},
        ],
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects/1/views/12/buckets",
        json=[
            {"id": 5, "title": "Todo", "count": 2},
            {"id": 6, "title": "In Progress", "count": 1},
        ],
    )
    buckets = await client.list_buckets(1)

    requests = httpx_mock.get_requests()
    assert requests[0].url.path.endswith("/projects/1/views")
    assert requests[1].url.path.endswith("/projects/1/views/12/buckets")
    assert buckets == [
        {"id": 5, "title": "Todo", "count": 2},
        {"id": 6, "title": "In Progress", "count": 1},
    ]


async def test_create_task_puts_title_description_due_date(httpx_mock, client):
    httpx_mock.add_response(
        method="PUT",
        url=f"{BASE_URL}/api/v1/projects/1/tasks",
        json={"id": 123, "title": "Fix garage door", "project_id": 1},
    )
    task = await client.create_task(
        1,
        "Fix garage door",
        description="The left hinge is loose",
        due_date="2026-04-01",
    )

    request = httpx_mock.get_requests()[0]
    assert request.method == "PUT"
    _assert_bearer_header(request)
    body = json.loads(request.content)
    assert body == {
        "title": "Fix garage door",
        "description": "The left hinge is loose",
        "due_date": "2026-04-01",
    }
    assert task["id"] == 123


async def test_create_task_omits_unset_fields(httpx_mock, client):
    httpx_mock.add_response(
        method="PUT",
        url=f"{BASE_URL}/api/v1/projects/1/tasks",
        json={"id": 124, "title": "Minimal"},
    )
    await client.create_task(1, "Minimal")

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {"title": "Minimal"}


async def test_set_bucket_posts_bucket_id(httpx_mock, client):
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/api/v1/tasks/123",
        json={"id": 123, "bucket_id": 6},
    )
    task = await client.set_bucket(123, 6)

    request = httpx_mock.get_requests()[0]
    assert request.method == "POST"
    assert request.url.path.endswith("/tasks/123")
    _assert_bearer_header(request)
    assert json.loads(request.content) == {"bucket_id": 6}
    assert task["bucket_id"] == 6


async def test_add_comment_puts_comment_text(httpx_mock, client):
    httpx_mock.add_response(
        method="PUT",
        url=f"{BASE_URL}/api/v1/tasks/123/comments",
        json={"id": 9, "comment": "Started working on this."},
    )
    comment = await client.add_comment(123, "Started working on this.")

    request = httpx_mock.get_requests()[0]
    assert request.method == "PUT"
    assert request.url.path.endswith("/tasks/123/comments")
    _assert_bearer_header(request)
    assert json.loads(request.content) == {"comment": "Started working on this."}
    assert comment["id"] == 9


async def test_list_tasks_filters_client_side(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects/1/tasks",
        json=[
            {"id": 1, "title": "Fix garage door", "done": False, "bucket_id": 6},
            {"id": 2, "title": "Buy milk", "done": False, "bucket_id": 5},
            {"id": 3, "title": "Old chore", "done": True, "bucket_id": 6},
            {"id": 4, "title": "Garage sale prep", "done": False, "bucket_id": 5},
        ],
    )
    tasks = await client.list_tasks(1, search="garage", bucket_id=6)

    assert [t["id"] for t in tasks] == [1]


async def test_list_tasks_hides_done_by_default(httpx_mock, client):
    done_payload = [
        {"id": 1, "title": "Open", "done": False, "bucket_id": None},
        {"id": 3, "title": "Closed", "done": True, "bucket_id": None},
    ]
    httpx_mock.add_response(
        method="GET", url=f"{BASE_URL}/api/v1/projects/1/tasks", json=done_payload
    )
    httpx_mock.add_response(
        method="GET", url=f"{BASE_URL}/api/v1/projects/1/tasks", json=done_payload
    )
    tasks = await client.list_tasks(1)
    assert [t["id"] for t in tasks] == [1]

    tasks = await client.list_tasks(1, include_done=True)
    assert [t["id"] for t in tasks] == [1, 3]


async def test_list_tasks_caps_at_limit(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects/1/tasks",
        json=[{"id": i, "title": f"T{i}", "done": False} for i in range(10)],
    )
    tasks = await client.list_tasks(1, limit=3)
    assert len(tasks) == 3


async def test_api_error_raises_upstream_error(httpx_mock, client):
    from ninmu.clients.vikunja import UpstreamError

    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects",
        status_code=403,
        json={"message": "nope"},
    )
    with pytest.raises(UpstreamError, match="403"):
        await client.list_projects()
