# Container image for registries that build MCP servers from source (Smithery, Glama).
# Runs the stdio server; credentials arrive as environment variables.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENTRYPOINT ["uv", "run", "--no-sync", "kstock-mcp"]
