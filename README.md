# Ninmu (任務)

Self-hosted MCP server exposing a **focused tool set** for [Vikunja](https://vikunja.io)
tasks and [NoteDiscovery](https://github.com/gamosoft/NoteDiscovery) notes.
Built for local LLMs with small context windows: instead of the ~80 tools the
upstreams' own MCP servers expose, Ninmu offers exactly **21 terse tools** whose
responses are short, pre-summarized strings.

Ninmu talks to both upstreams through their plain REST APIs — it does not proxy
their MCP endpoints.

## Tools

| Tool | Purpose |
|------|---------|
| `project_list` | List Vikunja projects (id + title) |
| `bucket_list` | List a project's kanban buckets (the valid "statuses") with ids |
| `task_create` | Create a task in a project (title, optional description and due date) |
| `task_list` | Find tasks (search text, bucket filter, done hidden by default) |
| `task_get` | Fetch one task by id (title, description, due date, bucket, priority) |
| `task_update` | Update a task's title, description, due date, or priority |
| `task_set_bucket` | Change a task's status by moving it to a kanban bucket |
| `task_set_done` | Mark a task done, or reopen it |
| `task_comment` | Add a comment to a task |
| `task_delete` | Permanently delete a task by id |
| `task_search` | Search tasks across ALL projects by text (finds the project for you) |
| `task_overview` | Overdue + due-today + due-within-N-days tasks across all projects |
| `folder_list` | List note folders with note counts |
| `note_list` | List notes in a folder (paginated, default 20) |
| `note_get` | Read a note's full markdown content by path (long notes clipped) |
| `note_search` | Full-text search across notes (paths, line numbers, snippets) |
| `note_create` | Create a note (folder + name + content, optional tags) |
| `note_append` | Append content to an existing note (optional timestamp) |
| `note_edit` | Replace one exact occurrence of text in a note |
| `note_move` | Move or rename a note |
| `note_delete` | Permanently delete a note by path |

Responses are terse ("Created task #123 \"Fix garage door\" in project 1."),
list endpoints default to 20 items with a hard max of 100 and tell you when
results were truncated.

## Notes on behavior

- **Status = kanban bucket.** Vikunja has no todo/in-progress/done status
  field; `bucket_list` discovers a project's buckets and `task_set_bucket`
  moves a task between them.
- **`folder_list` derives folders from the note graph**, so empty folders do
  not appear (NoteDiscovery's REST API has no folder-list endpoint).
- **Tags are YAML frontmatter.** `note_create` with tags prepends
  `tags: [...]` frontmatter, which is how NoteDiscovery models tags.

## Deployment

```bash
cp example.env .env   # fill in URLs and tokens
docker compose up -d
```

The MCP endpoint is served over streamable HTTP at `http://<host>:9093/mcp`
(see `docker-compose.yaml`). Optional bearer auth via `NINMU_AUTH_TOKEN`.

### Connecting a client

```json
{
  "mcpServers": {
    "ninmu": {
      "type": "http",
      "url": "http://192.168.178.10:9093/mcp"
    }
  }
}
```

With `NINMU_AUTH_TOKEN` set, add `"headers": {"Authorization": "Bearer <token>"}`.

## Local development

```bash
just          # format, lint, isort, test
just check    # type check + format/lint verification
just test     # pytest
just run      # run the server locally (uses .env)
```

## Configuration

See `example.env` for every variable: `VIKUNJA_URL`/`VIKUNJA_TOKEN`
(required), `NOTEDISCOVERY_URL` (required) and `NOTEDISCOVERY_API_KEY`
(optional — omit when NoteDiscovery runs without authentication), plus
optional `NINMU_AUTH_TOKEN`, `LIST_DEFAULT_LIMIT`, `LIST_MAX_LIMIT`,
`CORS_ORIGINS`.
