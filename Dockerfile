# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Dependencies first, in their own layer: they change far less often than source,
# so this keeps rebuilds fast when only src/ changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src/ ./src/
# --no-editable: install the package for real (copied into site-packages) instead of an
# editable link back to /app/src, since the runtime stage below only copies .venv, not src/.
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 gitlab-release \
    && useradd --uid 1000 --gid gitlab-release --no-create-home --shell /usr/sbin/nologin gitlab-release

COPY --from=builder --chown=gitlab-release:gitlab-release /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"

USER gitlab-release
WORKDIR /app

ENTRYPOINT ["gitlab-release"]
CMD ["--help"]
