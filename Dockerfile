FROM python:3.12-slim

# Update packages to fix security vulnerabilities
RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the package
COPY pyproject.toml README.md ./
COPY rigol_dho_mcp ./rigol_dho_mcp
RUN pip install --no-cache-dir .

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
