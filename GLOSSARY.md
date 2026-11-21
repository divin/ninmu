# Glossary

Terms used in Ninmu's code, tests, and documentation.

## Bucket

A column of a Vikunja project's kanban view. Ninmu treats buckets as a task's
**status** (todo → in progress → done), because Vikunja has no dedicated
status field. Discovered with `bucket_list`, changed with `task_set_bucket`.

## Terse response

The deliberately short string each tool returns (e.g. `Created task #123
"Fix garage door" in project 1.`). The point of Ninmu: minimize tokens spent
in a small local LLM's context window.

## Page limit

The number of items a list tool (`task_list`, `note_list`) returns. Defaults
to `LIST_DEFAULT_LIMIT` (20), hard-capped at `LIST_MAX_LIMIT` (100).

## Upstream

One of the two services Ninmu wraps: Vikunja (tasks) or NoteDiscovery
(notes). Ninmu talks to each via its REST API, never via its MCP endpoint.

## Frontmatter tags

NoteDiscovery models tags as YAML frontmatter (`tags: [a, b]`) at the top of
a note. `note_create` writes this automatically when tags are passed.

## Guard

The `_guard` helper in `server.py` that awaits an upstream call and converts
an `UpstreamError` into a FastMCP `ToolError` so clients get a clean message.
