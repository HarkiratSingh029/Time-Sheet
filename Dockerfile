# TS Timesheets — single-stage image.
#
# Single stage on purpose: there is no bundler and no node in the runtime, so a builder
# stage would buy nothing but a longer build (docs/ARCHITECTURE.md §2).

FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies resolve from pyproject alone, so this layer survives source changes.
COPY pyproject.toml README.md ./
RUN mkdir -p backend && touch backend/__init__.py && pip install --no-cache-dir .

COPY backend/ backend/
COPY frontend/ frontend/
COPY brands/ brands/
# The app runs `alembic upgrade head` at startup, so the revisions ship with it.
COPY alembic.ini ./
COPY migrations/ migrations/

# The image runs as a non-root user; data/ is a mounted volume it must be able to write.
RUN useradd --create-home --uid 10001 timesheets \
    && mkdir -p /app/data/uploads \
    && chown -R timesheets:timesheets /app
USER timesheets

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request, sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"

CMD ["uvicorn", "backend.app.main:build", "--factory", "--host", "0.0.0.0", "--port", "8000"]
