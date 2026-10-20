FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.11.23 && uv sync --locked --no-dev --no-editable
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["dark-mcp", "serve"]
