# The image exists mainly to bring WeasyPrint's native dependencies along —
# Pango, cairo and gdk-pixbuf cannot be installed with pip.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch: the GPU build is several gigabytes and buys nothing for
# embedding a few hundred short comments.
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY reference ./reference

RUN useradd --create-home --uid 10001 hgsfp \
    && mkdir -p /app/data /app/models \
    && chown -R hgsfp:hgsfp /app
USER hgsfp

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# One worker on purpose: SQLite has a single writer and the analysis job cache
# lives in-process.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
