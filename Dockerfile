# syntax=docker/dockerfile:1.7
# One image for the pipeline jobs and the Streamlit dashboard.
#   docker build -t india-etf-momentum .
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

# Code, config and the pre-registration. The project is installed editable on
# purpose: modules locate config/, data/ and results/ relative to /app.
COPY src ./src
COPY config ./config
COPY HYPOTHESIS.md README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM base AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY tests ./tests
RUN uv run --frozen pytest -q tests


FROM base AS runtime
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data /app/results \
    && chown -R app:app /app/data /app/results
USER app
ENV PATH="/app/.venv/bin:$PATH"
VOLUME ["/app/data", "/app/results"]
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4)" || exit 1
CMD ["streamlit", "run", "src/india_etf_momentum/dashboard.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
