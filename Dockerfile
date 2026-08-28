# syntax=docker/dockerfile:1

# The venv lives outside /app because Compose bind-mounts the source over /app
# and would otherwise shadow it.
FROM python:3.14-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM deps AS prod-deps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

FROM deps AS dev-deps
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

FROM python:3.14-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=core.settings
RUN useradd --create-home --uid 1000 app
WORKDIR /app
# WORKDIR creates /app as root before the COPY below runs, and --chown on
# COPY only applies to the copied contents, not the directory it already
# created. Without this, user `app` can't create new paths under /app
# (e.g. collectstatic's staticfiles/ dir) at runtime.
RUN chown app:app /app
COPY --chown=app:app . /app
EXPOSE 8000

FROM app AS dev
COPY --from=dev-deps --chown=app:app /opt/venv /opt/venv
USER app
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM app AS runtime
COPY --from=prod-deps --chown=app:app /opt/venv /opt/venv
USER app
# DEBUG is unset here on purpose: collectstatic must run under the manifest
# storage backend so the manifest exists at runtime.
RUN SECRET_KEY=build-only ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput
CMD ["gunicorn", "core.wsgi:application", "-c", "gunicorn.conf.py"]
