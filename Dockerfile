FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV SCHOLARTRACE_DATA_DIR=/var/lib/scholartrace
RUN mkdir -p /var/lib/scholartrace
VOLUME ["/var/lib/scholartrace"]

EXPOSE 8000
CMD ["uvicorn", "scholartrace.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
