# syntax=docker/dockerfile:1.7
# One image for the pipeline jobs and the Streamlit dashboard.
#   docker build -t india-strategy-lab .
#   docker build --target test .          # runs the unit tests during the build

FROM python:3.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Kolkata
WORKDIR /app

# Dependencies first so code edits don't invalidate this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Code and the strategies (configs + pre-registrations). Installed editable on
# purpose: modules locate strategies/ and data/ relative to /app.
COPY src ./src
COPY strategies ./strategies
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM base AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY tests ./tests
RUN uv run --frozen pytest -q tests


FROM base AS runtime
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data /app/strategies
USER app
ENV PATH="/app/.venv/bin:$PATH"
VOLUME ["/app/data"]
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)" || exit 1
CMD ["streamlit", "run", "src/strategylab/app/dashboard.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
