# Security Audit Report: Rigol DHO MCP Server

**Date:** July 30, 2026
**Revision:** 3 — remediation pass; all findings from revision 2 are closed
**Repository:** rigol-dho-mcp
**Branch:** `local-ai-tooling`
**Scope:** `rigol_dho_mcp/server.py`, `rigol_dho_mcp/scpi.py`, `rigol_dho_mcp/cli.py`, `Dockerfile`, `compose.yml`, `.dockerignore`, `.env.example`, `.gitignore`, `pyproject.toml`, `requirements.lock`, `README.md`

---

## 🔒 Executive Summary

Revision 2 identified nine open findings, headlined by a SCPI command-injection path that let any MCP client execute arbitrary commands on the instrument **while `RIGOL_ENABLE_SCPI_RAW=0`** — defeating the control the project documents as its protection against exactly that.

**All nine are now fixed and verified.** The injection is closed at two independent layers (enumerated tool schemas, plus control-character rejection in the SCPI transport), the container runs read-only with zero Linux capabilities behind a loopback-only port, dependencies are hash-pinned, and the committed internal network details are gone.

Verification was empirical, not by inspection: injection payloads were fired at both layers, the wire bytes were captured to confirm nothing leaked through, and the hardened image was built and exercised. Evidence is in [Verification](#-verification) below.

The one thing that has **not** changed is the deliberate architectural position: the server still ships no built-in authentication. That is now handled by making the secure deployment the default (loopback binding + reverse-proxy guidance) rather than by adding a half-built auth layer. See [Residual Risk](#-residual-risk).

---

## 📊 Remediation Status

| # | Finding | Severity | Status |
|---|---|---|---|
| 1 | SCPI command injection bypasses the `RIGOL_ENABLE_SCPI_RAW` gate | 🔴 High | ✅ Fixed |
| 2 | No authentication on the HTTP transport | 🟠 Medium-High | ✅ Mitigated by default posture |
| 3 | Internal network details committed as `compose.yml` defaults | 🟡 Medium | ✅ Fixed |
| 4 | Container/compose hardening gaps vs. home-lab standards | 🟡 Medium | ✅ Fixed |
| 5 | Unpinned dependencies and floating base image | 🟡 Medium | ✅ Fixed |
| 6 | Device-controlled unbounded allocation in the SCPI reader | 🔵 Low-Medium | ✅ Fixed |
| 7 | Non-ASCII input raises an unhandled `UnicodeEncodeError` | 🔵 Low | ✅ Fixed |
| 8 | Connection errors disclose scope host/port to clients | 🔵 Low | ✅ Fixed |
| 9 | `.dockerignore` git-ignored — build context leaks in fresh clones | 🔵 Low | ✅ Fixed |
| 10 | `env_file` made a missing `.env` a hard failure | ℹ️ Usability | ✅ Fixed (found during remediation) |

---

## 🔧 What Changed

### 1. SCPI command injection — 🔴 High → ✅ Fixed

Closed at two independent layers, so neither is a single point of failure.

**Transport (`scpi.py`).** A new `_validate_command()` runs before any socket work:

```python
command = command.strip()
if not command.isascii():
    raise ScpiError(f"SCPI commands must be ASCII: {command!r}")
bad = sorted({c for c in command if ord(c) < 0x20 or ord(c) == 0x7F})
if bad:
    raise ScpiError(
        f"Refusing to send SCPI containing control characters {bad!r} — "
        f"an embedded newline would be sent as a separate command: {command!r}"
    )
```

All control characters are rejected, not just `\r\n`, so the fix stays closed if the instrument ever treats another byte as a delimiter. Validating before `_ensure()` means a rejected command leaves the connection untouched and in sync — no response-stream desync. This is the load-bearing fix: it closes the whole class of bug and covers `cli.py`, which shares the client.

**Schema (`server.py`).** The four remaining free-form strings are now enumerated:

```python
MemoryDepth  = Literal["AUTO", "1k", "10k", "100k", "1M", "5M", "10M", "25M", "50M"]
CursorSource = Literal["CHAN1"…"CHAN4", "MATH1"…"MATH4"]
```

applied to `configure_acquisition.memory_depth` and `configure_cursors.{source, track_source_a, track_source_b}`. `CursorSource` includes `MATH1-4` — a superset of what the tool descriptions documented — so this closes the hole without narrowing real functionality. These enums also reach the MCP client as JSON Schema `enum` constraints, which makes the tools easier for a model to call correctly.

A comment at the type definitions records the rule for future tools: **never interpolate a free-form `str` into a SCPI command string.**

### 2. No HTTP authentication — 🟠 Medium-High → ✅ Mitigated

No authentication was added to the application. Instead the default deployment posture is now safe, and the guidance is specific:

- **`compose.yml` publishes on loopback only:** `"${MCP_BIND_ADDRESS:-127.0.0.1}:${MCP_PORT:-8000}:…"`. LAN exposure is now an explicit opt-in (`MCP_BIND_ADDRESS=0.0.0.0`) rather than the out-of-the-box behaviour.
- **Traefik labels are provided, commented out,** with the correct middleware guidance — including the trap that matters here: this is a pure-API service, so `authentik_domain@file` forward-auth **will break every MCP client**, because MCP clients cannot complete an interactive browser login. A `basicauth` or bearer-token middleware is the right choice.
- **README now states plainly that CORS is not access control** and that DNS-rebinding protection is not authentication.

### 3. Committed internal network details — 🟡 Medium → ✅ Fixed

`192.168.2.10:8698` and `local-services.local:8698` are gone; both allowlists default to empty:

```yaml
MCP_ALLOWED_ORIGINS: ${MCP_ALLOWED_ORIGINS:-}
MCP_ALLOWED_HOSTS: ${MCP_ALLOWED_HOSTS:-}
```

This also removes the port-mismatch footgun (allowlist on `8698` vs. `MCP_PORT` `8000`) that made the shipped config reject every request and pushed users toward disabling protection. Since a rejection is now a *configuration* state rather than a mystery, `.env.example` and the README name the exact symptom — `421 Misdirected Request` — and the fix.

### 4. Container hardening — 🟡 Medium → ✅ Fixed

Against the home-lab compose standards:

| Requirement | Before | After |
|---|---|---|
| `restart: unless-stopped` | ✅ | ✅ |
| `init: true` | ❌ | ✅ |
| `security_opt: [no-new-privileges:true]` | ❌ | ✅ |
| Healthcheck (`curl --fail`, all four timings) | ❌ | ✅ |
| Traefik labels | ❌ | ✅ (commented, with SSO caveat) |

Beyond the standards: `cap_drop: [ALL]`, `read_only: true` with `tmpfs: /tmp`, and `mem_limit: ${MCP_MEM_LIMIT:-2g}`.

The healthcheck required a real endpoint — `python:slim` ships no `curl` (now installed), and MCP's `/mcp` returns `4xx` to a plain `GET`, so `curl --fail` could never pass against it. A dedicated `/health` route was added to the Starlette app, deliberately placed **outside** the mounted MCP app so it isn't subject to the `Host` allowlist (the healthcheck connects to `127.0.0.1`, which won't be in `MCP_ALLOWED_HOSTS`). It reports HTTP liveness only — it does not probe the scope, because a powered-off bench instrument is a normal state and folding it in would cause a restart loop — and returns no detail about the scope or host.

### 5. Supply chain — 🟡 Medium → ✅ Fixed

- Base image pinned by digest: `python:3.12-slim@sha256:57cd7c3a…710de`, with the refresh command in a comment.
- New **`requirements.lock`**, generated for Linux with `--generate-hashes`, installed via `pip install --require-hashes`. The dependency tree is now reproducible and tamper-evident; the app itself installs with `--no-deps`. Pinned versions include `mcp==1.29.0`, `uvicorn==0.52.0`, `starlette==1.3.1`.
- `pyproject.toml` bounded at both ends (`uvicorn` previously had no constraint at all).
- Lock install is a separate, earlier layer, so it caches across source edits.

### 6. Unbounded allocations — 🔵 Low-Medium → ✅ Fixed

`_MAX_LINE` (1 MiB) caps `_read_line()`; `_MAX_BLOCK` (64 MiB) caps the TMC payload — well above a full 50M-point deep-memory chunk. Header parsing is wrapped so a malformed header raises `ScpiError` instead of a bare `ValueError` escaping past every caller's error handling, and `_reset_and_raise` is now typed `NoReturn` so the socket-reset-and-raise contract is explicit.

### 7. Non-ASCII crash — 🔵 Low → ✅ Fixed

Folded into the `_validate_command()` ASCII check, converting an unhandled `UnicodeEncodeError` into a normal `ScpiError`.

### 8. Host/port disclosure — 🔵 Low → ✅ Fixed

The address moved from the exception to the log:

```python
logger.error("Could not connect to scope at %s:%s — %s", self.host, self.port, e)
raise ScpiError("Could not connect to the oscilloscope (address and underlying "
                "error are in the server log). …")
```

Operators keep the full diagnostic via `docker compose logs`; an unauthenticated caller no longer learns the scope's internal IP.

### 9. `.dockerignore` — 🔵 Low → ✅ Fixed

Removed from `.gitignore` and committed. Excludes `.env*`, `.git/`, caches, and CLI capture artefacts. (`capture_*.png` / `shot_*.png` were also added to `.gitignore` — `cli.py` writes those into the working directory, and a scope screenshot can show whatever is on the bench.)

### 10. Missing `.env` was a hard failure — ℹ️ Found during remediation

`docker compose config` failed outright on a fresh clone because `env_file: [.env]` is mandatory by default. Now `required: false`, so the stack starts and can explain itself rather than erroring before any of the guidance is reachable.

---

## ✅ Verification

Not inspection — each fix was exercised.

**Injection, transport layer.** Payloads fired at `_validate_command`, plus an end-to-end test against a fake scope socket capturing the actual wire bytes:

```
REJECT INJECTION via newline      -> control characters ['\n']
REJECT INJECTION via CRLF         -> control characters ['\n', '\r']
REJECT INJECTION via cursor source-> control characters ['\n']
REJECT non-ASCII                  -> SCPI commands must be ASCII
ACCEPT ':ACQuire:MDEPth AUTO' / '  :RUN  ' -> ':RUN'

bytes on wire: b':ACQuire:MDEPth AUTO\n'      # injected command never sent
```

**Injection, schema layer.** Via `mcp.call_tool`, with `RIGOL_ENABLE_SCPI_RAW` unset:

```
memory_depth='AUTO\n*RST'          -> REJECTED (validation error)
memory_depth='50M'                 -> passes schema, reaches SCPI layer
source='CHAN1\n:SYSTem:PRESet'     -> REJECTED (validation error)
source='CHAN1'                     -> passes schema, reaches SCPI layer
scpi_command exposed               -> False
```

Legitimate values fail only on the unset `RIGOL_HOST`, which proves the schema discriminates rather than blanket-rejecting.

**Container runtime.** Image built, then run with the compose security profile:

```
id                 -> uid=1000(appuser) gid=1000(appuser)
CapEff             -> 0000000000000000        # zero capabilities
touch /probe       -> Read-only file system
curl --fail /health-> {"status":"ok"} (exit=0)
```

**Access control live.** With an empty allowlist and protection on:

```
POST /mcp     -> HTTP 421   (rejected on Host header — fails closed)
GET  /health  -> HTTP 200   (exempt, as designed)
```

**Compose renders clean** with no `.env` present: loopback `host_ip: 127.0.0.1`, empty allowlists, `read_only`, `cap_drop: ALL`, `init`, `no-new-privileges`, healthcheck, `mem_limit`.

---

## ⚠️ Residual Risk

Accepted, not overlooked.

**No built-in authentication.** The server still has none. Loopback-by-default plus proxy guidance makes the *default* safe, but anyone setting `MCP_BIND_ADDRESS=0.0.0.0` without a proxy has an unauthenticated instrument on the LAN. Adding real auth (bearer token middleware in `server.py`) remains the only way to make the application safe standalone. Deliberately not done here: a hand-rolled auth layer in an MCP server is easy to get subtly wrong, and a proxy does it properly.

**`get_waveform` memory amplification.** *Newly identified during remediation.* `mode="memory"` at 50M points builds a Python list of 50M floats — roughly 1.5–2 GB (24 bytes per float object plus 8 bytes per list pointer), from a ~50 MB byte buffer. This is legitimate functionality, so it wasn't capped in code; `mem_limit: 2g` bounds the blast radius and the tradeoff is documented in `compose.yml` and `.env.example`. A future fix would stream and decimate incrementally instead of materialising every sample. **On an exposed endpoint this is a cheap OOM trigger** — another reason not to set `MCP_BIND_ADDRESS=0.0.0.0` unprotected.

**`RIGOL_ENABLE_SCPI_RAW=1` is still arbitrary SCPI.** By design. The gate is now meaningful (finding 1), but enabling it grants exactly what it says.

**`_MAX_BLOCK` is a heuristic.** 64 MiB comfortably exceeds a 50M-point chunk at `_RAW_CHUNK = 250,000`. A future firmware returning larger blocks would need it raised.

**stdio transport bypasses every HTTP control** — correct by design; the trust boundary there is the local process that spawns the server.

**This file is a public record of the project's security posture.** Now that findings are closed that's far less sensitive than it was at revision 2, but keep it in mind before making the repository public.

---

## 📌 Recommended Next Steps

Nothing outstanding from the audit. In rough value order, if you want to keep going:

1. **Bearer-token middleware in `server.py`** — the only change that makes the HTTP transport safe without a reverse proxy.
2. **Stream `get_waveform` deep-memory reads** — removes the residual OOM vector and makes 50M-point captures practical under a tighter `mem_limit`.
3. **Regenerate `requirements.lock` on a schedule** — pinning stops silent drift, which also means security patches no longer arrive on their own.
4. **A regression test for the injection fix** — the repo has no test suite; `_validate_command` is a natural first unit test so this can't quietly regress.
5. **Migrate to the MCP v2 API** and drop the `mcp<2.0.0` pin, so security fixes in the SDK stay reachable.
