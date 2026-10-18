FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.11.23
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev && useradd --create-home --uid 10001 darkmcp \
    && mkdir /data && chown darkmcp:darkmcp /data
ENV PATH="/app/.venv/bin:$PATH" DARK_MCP_DATA_DIR=/data
USER darkmcp
ENTRYPOINT ["dark-mcp"]
CMD ["serve"]
