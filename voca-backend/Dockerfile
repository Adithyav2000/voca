# VOCA Backend — Production-ready multi-stage Dockerfile
# Python 3.12, Poetry, no venv in container

FROM python:3.12-slim AS builder

WORKDIR /app

# Install Poetry and disable venv creation in container
RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false

COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev by default)
RUN poetry install --no-interaction --no-ansi --no-root

# Final stage
FROM python:3.12-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Application code
COPY app ./app

# Non-root user
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Use h11 instead of httptools: OpenAI/ngrok sometimes send headers httptools rejects (400 "invalid header name")
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--http", "h11"]
