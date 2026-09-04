FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.12.3 \
    && uv sync --locked --no-dev --no-editable

ENV SCHOLARTRACE_DATA_DIR=/var/lib/scholartrace
ENV PATH="/app/.venv/bin:${PATH}"
RUN mkdir -p /var/lib/scholartrace
VOLUME ["/var/lib/scholartrace"]

EXPOSE 8000
CMD ["uvicorn", "scholartrace.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
