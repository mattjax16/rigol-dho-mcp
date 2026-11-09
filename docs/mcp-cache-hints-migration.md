# Plan: advertise tools/list cache hints (SEP-2549) so agent clients can defer connecting us

Status: plan only. No code change on any branch has been merged.
Measured on ai-agents, 2026-11-09.

## Why

Hermes (and any SEP-2549 client) can register an MCP server's tool manifest from an
on-disk schema cache and open the HTTP session **on the first tool call** instead of at
every start (`mcp_servers.<name>.lazy: true`). Hermes only does that when the cached
manifest is still fresh, and freshness comes from `ttlMs` on the `tools/list` result.

Live state on this box: every entry in `~/.hermes/cache/mcp_schema_cache.json` has
`ttl_ms: 0`, so `get_cached_entry()` computes `age_ms >= 0` → expired → the lazy path can
never engage and Hermes re-probes this server at every start. Fixing it is server-side.

`ttlMs`/`cacheScope` are result fields of the **2026-07-28** protocol era and exist only
in the `mcp` / `mcp-types` **2.x** Python SDK. This server runs `mcp==1.29.0`.

## What does not work (already tested, don't retry)

Stamping `ttlMs` onto a 1.x wire by abusing `ListToolsResult(extra="allow")` does get the
key onto the wire (`result keys: ['cacheScope','tools','ttlMs']`), but Hermes still
recorded `ttl_ms: 0`: the session negotiated `2025-06-18` and the 2.x client sieves
2026-era result fields off a legacy wire. The server has to *speak* the modern era.

Also unhelpful: the SDK model defaults `ttl_ms = 0` when a server sends nothing, and
Hermes reads that default as if we had sent it. A server that says nothing is treated as
"stale immediately". That is a Hermes bug (fixable with one field-presence check) but it
is not a reason to skip advertising a real hint here.

## Target

- `mcp` 2.3.0 (current stable, released 2026-10-02; `mcp-types` is version-locked to it).
- Hermes' own client is already exactly 2.3.0, so client and server match.
- Hint: `ttl_ms = 86_400_000` (24 h), `cache_scope = "private"`. The tool set changes
  only on redeploy, and Hermes refreshes the hint on every live connect it makes.

## Verified 1.x → 2.x API mapping (checked against mcp 2.3.0 on 2026-11-09)

| this repo uses today | mcp 2.x equivalent |
| --- | --- |
| `from mcp.server.fastmcp import FastMCP, Image` | `from mcp.server.mcpserver import MCPServer, Image` (`mcp.server.fastmcp` now **raises** `ModuleNotFoundError` on purpose) |
| `FastMCP(name, host=…, port=…, transport_security=…)` | `MCPServer(name)` — **`host` / `port` / `transport_security` are not constructor params any more**; they move to `run(...)` / `streamable_http_app(...)` |
| `mcp = FastMCP(...)` at import time | same pattern works; `cache_hints=` is a constructor param |
| `@mcp.tool()` + `Annotated[…, Field(...)]` | unchanged |
| `mcp.streamable_http_app()` | still present |
| `mcp.session_manager.run()` (lifespan) | attribute still present — **verify semantics during the port**, the 2.x app owns its session manager differently |
| `from mcp.server.transport_security import TransportSecuritySettings` | unchanged (exported) |
| `mcp.run(transport=...)` | `run(transport="streamable-http", host=…, port=…, streamable_http_path="/mcp", transport_security=…)` |

Cache-hint API, verified:

```python
from mcp.server.caching import CacheHint          # CacheHint(ttl_ms: int = 0, scope: "public"|"private" = "private")
# cacheable methods: tools/list, prompts/list, resources/list,
#                    resources/templates/list, resources/read, server/discover
mcp = MCPServer(
    "rigol-dho800",
    cache_hints={"tools/list": CacheHint(ttl_ms=86_400_000, scope="private")},
)
```

## Steps

1. Branch `mcp-cache-hints-plan` (this branch) carries the plan; the implementation goes
   on `mcp-2x-migration`.
2. `pyproject.toml`: `mcp>=1.29.0,<2.0.0` → `mcp>=2.3.0,<3.0.0`. Keep the exact-pinned
   lock discipline.
3. Regenerate the hash-verified lock (the command is already in `Dockerfile`):
   `uv pip compile --generate-hashes --python-platform x86_64-unknown-linux-gnu --python-version 3.12 -o requirements.lock pyproject.toml`
   Diff the lock by hand: `mcp` 2.3.0 + `mcp-types` 2.3.0 land, `httpx`/`starlette`/
   `pydantic` may move. This is the review surface.
4. `rigol_dho_mcp/server.py`: rename the import, move `host`/`port`/`transport_security`
   out of the constructor, add `cache_hints` (env-driven, see step 5), re-check the
   streamable-HTTP ASGI wiring (this repo builds its own Starlette app + `CORSMiddleware`
   + `/health`, so the mount call is the part most likely to change).
5. Env knob: `MCP_TOOLS_CACHE_TTL_MS` (default `86400000`, `0` disables). One line of
   plumbing into the `cache_hints` mapping. Document in `compose.yml` and `.env.example`.
6. README: one line under the client-setup section noting the server advertises a 24 h
   `tools/list` freshness hint.

## Verification (all local, no scope needed for the tool list)

1. Wire probe (the script that proved the diagnosis) — expect
   `result keys: ['tools','ttlMs','cacheScope']` **and** a modern negotiated version:
   `python3 probe_tools_list.py http://127.0.0.1:8000/mcp` (script in the ai-setup
   tracker doc: `docs/hermes-lazy-mcp-cache-hints.md`).
2. Hermes side, after deploy: set `mcp_servers.rigol.protocol: stateless` and keep
   `lazy: true`, restart the user gateway, and look for
   `MCP server 'rigol' (lazy): registered N tool(s) from schema cache` on the *second*
   start. First start stays eager (it fills the cache).
3. Other consumers must be re-checked, because the protocol era changes: pi (it talks to
   this endpoint too), `mcp-inspector` (running on Local-Services), `mcpo` if anything
   fronts this server through it, and the `rigol-dho-scope-control` skill's flows.
4. Container healthcheck (`/health`) and SCPI behaviour are unaffected by the SDK bump —
   but a real measurement against the scope should be run once before merging.

## Rollback

Revert the SDK pin + the import changes (one commit each). The image is built from the
lockfile, so an untouched lock means an untouched dependency tree — keeping the lock
regeneration in its own commit makes `git revert` of the dependency bump clean.

## Open questions before implementation

- Does `MCPServer`'s `session_manager`/lifespan story still allow this repo's custom
  Starlette mount? (Unverified; the reason this is a plan and not a patch.)
- Do pi and mcp-inspector negotiate the modern era happily, or do we need the server to
  keep serving legacy clients side by side? (The 2.x SDK does serve legacy sessions, but
  hints then get sieved off for them — harmless.)
- Is `server/discover` the only route to a 2026-07-28 session in practice, i.e. must
  every client set a "stateless probe" style option? See the ai-setup tracker doc: this is
  the single unresolved fact that decides whether the whole programme is worth it.
