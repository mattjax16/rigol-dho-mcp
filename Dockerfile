# Pinned by digest, not just the floating :3.12-slim tag, so a rebuild of a
# given commit resolves the same base image. Bump deliberately:
#   docker pull python:3.12-slim && \
#   docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}'
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

# Upgrade base packages for CVE patches. curl is needed by the compose
# healthcheck (python:slim doesn't ship it).
RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies come from a hash-verified lockfile rather than being resolved
# fresh from PyPI at build time: the tree is then reproducible and tamper-
# evident, and a compromised release of a transitive dependency can't land in
# the image on the next rebuild. Regenerate with:
#   uv pip compile --generate-hashes --python-platform x86_64-unknown-linux-gnu \
#     --python-version 3.12 -o requirements.lock pyproject.toml
# Installed before the source is copied so the layer caches across code edits.
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# --no-deps: everything is already installed, pinned, from the lockfile above.
COPY pyproject.toml README.md ./
COPY rigol_dho_mcp ./rigol_dho_mcp
RUN pip install --no-cache-dir --no-deps .

# Run as non-root
RUN useradd --create-home appuser
USER appuser

# Defaults: HTTP transport so the container is directly reachable.
# Override MCP_TRANSPORT=stdio to use it with `docker run -i` instead.
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    RIGOL_PORT=5555

EXPOSE 8000

ENTRYPOINT ["rigol-dho-mcp"]
