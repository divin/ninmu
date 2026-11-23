"""Tests for the Vikunja REST client.

Every test pins the exact request ninmu sends (method, path, JSON body)
and the terse value the client returns, so upstream API changes surface
as test failures instead of silent breakage.
"""

from __future__ import annotations

import json

import httpx
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


async def test_create_task_passes_bucket_id(httpx_mock, client):
    httpx_mock.add_response(
        method="PUT",
        url=f"{BASE_URL}/api/v1/projects/1/tasks",
        json={"id": 125, "title": "Bucketed", "bucket_id": 6},
    )
    await client.create_task(1, "Bucketed", bucket_id=6)

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {"title": "Bucketed", "bucket_id": 6}


async def test_search_tasks_sends_query_and_done_filter(httpx_mock, client):
    url = str(
        httpx.URL(f"{BASE_URL}/api/v1/tasks").copy_merge_params(
            {"s": "garage", "filter": "done = false", "per_page": "20"}
        )
    )
    httpx_mock.add_response(
        method="GET",
        url=url,
        json=[{"id": 1, "title": "Fix garage door", "project_id": 14}],
    )
    tasks = await client.search_tasks("garage", limit=20)

    request = httpx_mock.get_requests()[0]
    assert request.url.path.endswith("/api/v1/tasks")
    _assert_bearer_header(request)
    assert request.url.params["s"] == "garage"
    assert request.url.params["filter"] == "done = false"
    assert request.url.params["per_page"] == "20"
    assert tasks[0]["id"] == 1


async def test_search_tasks_omits_filter_when_including_done(httpx_mock, client):
    url = str(
        httpx.URL(f"{BASE_URL}/api/v1/tasks").copy_merge_params(
            {"s": "garage", "per_page": "100"}
        )
    )
    httpx_mock.add_response(method="GET", url=url, json=[])
    await client.search_tasks("garage", include_done=True)

    request = httpx_mock.get_requests()[0]
    assert "filter" not in request.url.params


async def test_search_tasks_passes_filter_expression_and_sort(httpx_mock, client):
    url = str(
        httpx.URL(f"{BASE_URL}/api/v1/tasks").copy_merge_params(
            {
                "filter": "done = false && due_date < now+7d",
                "per_page": "100",
                "sort_by": "due_date",
            }
        )
    )
    httpx_mock.add_response(method="GET", url=url, json=[])
    await client.search_tasks(
        filter_expr="due_date < now+7d",
        limit=100,
        sort_by="due_date",
    )

    request = httpx_mock.get_requests()[0]
    assert request.url.params["filter"] == "done = false && due_date < now+7d"
    assert request.url.params["per_page"] == "100"
    assert request.url.params["sort_by"] == "due_date"
    assert "s" not in request.url.params


async def test_set_bucket_resolves_project_and_kanban_view(httpx_mock, client):
    """v2 bucket moves are per-view: task → project → kanban view → move."""
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/tasks/123",
        json={"id": 123, "title": "Fix garage door", "project_id": 7},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects/7/views",
        json=[
            {"id": 11, "view_kind": "list"},
            {"id": 12, "view_kind": "kanban"},
        ],
    )
    httpx_mock.add_response(
        method="POST",
        url=f"{BASE_URL}/api/v1/projects/7/views/12/buckets/6/tasks",
        json={"task_id": 123, "project_view_id": 12, "bucket_id": 6},
    )
    result = await client.set_bucket(123, 6)

    requests = httpx_mock.get_requests()
    assert [r.method for r in requests] == ["GET", "GET", "POST"]
    assert requests[0].url.path.endswith("/tasks/123")
    assert requests[1].url.path.endswith("/projects/7/views")
    assert requests[2].url.path.endswith("/projects/7/views/12/buckets/6/tasks")
    _assert_bearer_header(requests[2])
    assert json.loads(requests[2].content) == {
        "task_id": 123,
        "project_view_id": 12,
        "bucket_id": 6,
    }
    assert result["bucket_id"] == 6


async def test_set_bucket_raises_no_kanban_view_when_none_exists(httpx_mock, client):
    from ninmu.clients.vikunja import NoKanbanView

    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/tasks/123",
        json={"id": 123, "title": "T", "project_id": 7},
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects/7/views",
        json=[{"id": 11, "view_kind": "list"}],
    )
    with pytest.raises(NoKanbanView):
        await client.set_bucket(123, 6)


async def test_list_buckets_raises_no_kanban_view_when_none_exists(httpx_mock, client):
    from ninmu.clients.vikunja import NoKanbanView

    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/projects/1/views",
        json=[{"id": 11, "view_kind": "list"}],
    )
    with pytest.raises(NoKanbanView):
        await client.list_buckets(1)


async def test_get_task_fetches_by_id(httpx_mock, client):
    httpx_mock.add_response(
        method="GET",
        url=f"{BASE_URL}/api/v1/tasks/123",
        json={"id": 123, "title": "Fix garage door", "done": False},
    )
    task = await client.get_task(123)

    request = httpx_mock.get_requests()[0]
    assert request.method == "GET"
    assert request.url.path.endswith("/tasks/123")
    _assert_bearer_header(request)
    assert task["id"] == 123


async def test_update_task_gets_then_posts_merged_full_object(httpx_mock, client):
    """v2 task update is a full replacement, so the client fetches the
    task, merges the provided fields, and posts the whole object back."""
    original = {
        "id": 123,
        "title": "Old title",
        "description": "keep me",
        "project_id": 1,
        "done": False,
        "priority": 0,
        "due_date": "0001-01-01T00:00:00Z",
    }
    httpx_mock.add_response(
        method="GET", url=f"{BASE_URL}/api/v1/tasks/123", json=original
    )
    httpx_mock.add_response(
        method="POST", url=f"{BASE_URL}/api/v1/tasks/123", json=original
    )
    await client.update_task(123, title="Renamed", priority=3)

    requests = httpx_mock.get_requests()
    assert [r.method for r in requests] == ["GET", "POST"]
    _assert_bearer_header(requests[1])
    body = json.loads(requests[1].content)
    assert body["title"] == "Renamed"
    assert body["priority"] == 3
    assert body["description"] == "keep me"  # untouched field preserved


async def test_set_task_done_gets_then_posts_done_flag(httpx_mock, client):
    original = {"id": 123, "title": "T", "project_id": 1, "done": False}
    httpx_mock.add_response(
        method="GET", url=f"{BASE_URL}/api/v1/tasks/123", json=original
    )
    httpx_mock.add_response(
        method="POST", url=f"{BASE_URL}/api/v1/tasks/123", json=original
    )
    await client.set_task_done(123, True)

    requests = httpx_mock.get_requests()
    assert [r.method for r in requests] == ["GET", "POST"]
    assert json.loads(requests[1].content)["done"] is True


async def test_delete_task_sends_delete(httpx_mock, client):
    httpx_mock.add_response(
        method="DELETE",
        url=f"{BASE_URL}/api/v1/tasks/123",
        json={"message": "ok"},
    )
    await client.delete_task(123)

    request = httpx_mock.get_requests()[0]
    assert request.method == "DELETE"
    assert request.url.path.endswith("/tasks/123")
    _assert_bearer_header(request)


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
            {"id": 1, "title": "Fix garage door", "done": False},
            {"id": 2, "title": "Buy milk", "done": False},
            {"id": 3, "title": "Old chore", "done": True},
            {"id": 4, "title": "Garage sale prep", "done": False},
        ],
    )
    tasks = await client.list_tasks(1, search="garage")

    assert [t["id"] for t in tasks] == [1, 4]


async def test_list_tasks_hides_done_by_default(httpx_mock, client):
    done_payload = [
        {"id": 1, "title": "Open", "done": False},
        {"id": 3, "title": "Closed", "done": True},
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
