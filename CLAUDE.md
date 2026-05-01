# Claude Code context for netbox-mcp-rw

This file orients Claude Code (or other LLM coding agents) when working in this repository.

## What this is

A read–write MCP (Model Context Protocol) server that exposes NetBox's REST API as a set of
tools an LLM can call. Built on FastMCP. Runs over stdio.

- `server.py` — tool definitions, the `NETBOX_OBJECT_TYPES_BASE` allowlist, capability detection, FastMCP entrypoint.
- `netbox_client.py` — thin REST wrapper (`get`, `create`, `update`, `delete`, `bulk_*`). No NetBox SDK dependency — uses `requests` directly.
- `pyproject.toml` — `requires-python = ">=3.13"`, deps: `mcp[cli]`, `httpx`, `requests`. Managed with `uv`.

## Running locally

```bash
export NETBOX_URL=https://netbox.example.com/
export NETBOX_TOKEN=<api-token>
uv run server.py
```

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `NETBOX_URL` | Base URL of the NetBox instance (required) | — |
| `NETBOX_TOKEN` | API token (required) | — |
| `NETBOX_VERIFY_SSL` | Verify TLS certs (`true`/`false`/`1`/`0`) | `true` |
| `NETBOX_MCP_WRAP_LIST_RESULTS` | Wrap list returns as `{count, results}` so MCP clients don't treat lists as content blocks | `true` |
| `NETBOX_MCP_AUTO_SCHEME` | Auto-detect `http` vs `https` from the host | `false` |
| `NETBOX_MCP_AUTO_SCHEME_TIMEOUT_SEC` | Probe timeout when auto-scheme is on | `1.5` |
| `NETBOX_MCP_ENABLE_NETBOX` | Gate on whether NetBox tools register | (enabled by default) |

## Tool surface

All tools are registered via `@mcp.tool()` in `server.py`.

Read tools (all support `fields=[...]`, `limit`, `offset`, `brief` where applicable — **always pass `fields` to keep response payloads small**):

- `netbox_get_objects(object_type, filters, fields=None, limit=None, offset=None, brief=False)`
- `netbox_get_object_by_id(object_type, object_id, fields=None, brief=False)`
- `netbox_search_objects(query, object_type=None, fields=None, limit=50)` — `?q=` search across one or all common types; injects `__object_type__` on each result when no `object_type` is given
- `netbox_get_changelogs(filters, fields=None, limit=None, offset=None)`

Write tools:

- `netbox_create_object(object_type, data)`
- `netbox_update_object(object_type, object_id, data)` — PATCH semantics
- `netbox_delete_object(object_type, object_id)`
- `netbox_create_journal_entry(assigned_object_type, assigned_object_id, comments, kind="info")` — convenience wrapper; `assigned_object_type` is dotted notation (`dcim.device`, `virtualization.virtualmachine`, etc.)
- `netbox_set_interface_mac(interface_id, mac_address)` — version-aware MAC setter for **device interfaces** (`dcim/interfaces`). Writes to `interfaces.mac_address` on NetBox 3.x, creates a `dcim/mac-addresses` and assigns `primary_mac_address` on 4.x.
- `netbox_set_vm_interface_mac(vm_interface_id, mac_address)` — same behavior but targets **VM interfaces** (`virtualization/interfaces`). The two endpoints have independent schemas and ID spaces, so a separate tool is required to avoid silently writing to the wrong object.

Bulk tools:

- `netbox_bulk_create_objects(object_type, data)` — POST list
- `netbox_bulk_update_objects(object_type, data)` — PATCH list (each item must include `id`)
- `netbox_bulk_delete_objects(object_type, ids)` — DELETE list

## Conventions

### Object type strings

The `object_type` argument uses **URL-path notation** (`devices`, `ip-addresses`, `virtual-machines`,
`virtual-disks`, `journal-entries`). The full set is in `NETBOX_OBJECT_TYPES_BASE` at the top of
`server.py` — that dict is the source of truth. Do not maintain duplicate lists in tool docstrings;
point readers at the dict instead.

### Dotted notation appears in two places

When a value refers to a NetBox content type (rather than identifying a tool argument), it uses
dotted notation: `dcim.interface`, `dcim.frontport`, `virtualization.virtualmachine`. This shows up in:

- `cables` terminations: `{"object_type": "dcim.interface", "object_id": <id>}`
- `services`: `parent_object_type` + `parent_object_id`
- `journal-entries`: `assigned_object_type` + `assigned_object_id`

### Bulk operation contract

Bulk ops POST/PATCH/DELETE a JSON list to the **canonical endpoint** (no `/bulk/` suffix). This
matches NetBox 4.x. NetBox 3.x bulk operations (`/bulk/` route) are not supported.

### Capability detection

`server.py` runs `_detect_capabilities()` at startup to populate the `CAPABILITIES` dict. Tools that
behave differently across NetBox versions (notably MAC address handling) consult this dict rather
than hardcoding a NetBox major version.

## Testing changes

There is no automated test suite. To validate a change, exercise the modified module against a live
NetBox instance:

```python
import os, importlib.util
spec = importlib.util.spec_from_file_location("server", "server.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

from netbox_client import NetBoxRestClient
mod.netbox = NetBoxRestClient(url=os.environ["NETBOX_URL"], token=os.environ["NETBOX_TOKEN"])

# Now invoke the tool function directly:
result = mod.netbox_get_objects("devices", {}, fields=["id", "name"], limit=3)
```

Set `NETBOX_MCP_WRAP_LIST_RESULTS=false` if your test asserts on raw lists rather than the
`{count, results}` wrapper. Importing `server.py` does not call `mcp.run()` — that's gated by
`if __name__ == "__main__":`.

## When adding a new tool

1. Decide if a new entry in `NETBOX_OBJECT_TYPES_BASE` is enough (most cases) or if you need a
   dedicated wrapper (use a wrapper when the object requires a non-obvious payload shape — see
   `netbox_create_journal_entry` and the way `services` use `parent_object_type`).
2. Mirror the `fields`/`limit`/`offset` parameter style of existing read tools.
3. Use `_build_query_params(...)` to merge user filters with the optional knobs.
4. Update tool docstrings with a usage example. Do not paste the object-type list — point readers
   at `NETBOX_OBJECT_TYPES_BASE`.

## When adding a new object type

Just add the entry to `NETBOX_OBJECT_TYPES_BASE`. Tools that operate generically on `object_type`
will pick it up automatically. No docstring changes needed.

## Out of scope

- ORM / NetBox plugin mode — `NetBoxClientBase` defines the abstract interface but only the REST
  implementation exists.
- Caching — every call hits the API. If you need many cheap reads, use `fields=` aggressively.
- Async — the server is synchronous. FastMCP can run sync tools fine.
