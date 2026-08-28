# backend-devops-interview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the working prototype into a service a team can develop on and operate: one-command local setup, measured performance fixes on every read endpoint, and a container that deploys to Fly.io.

**Architecture:** One multi-stage Dockerfile produces a `dev` target (dev dependencies, autoreload) and a `runtime` target (production dependencies, gunicorn, collected static). Docker Compose wires the `dev` target to Postgres 16 for local work and CI; Fly deploys the `runtime` target. Settings become environment-driven so the same image runs in all three places.

**Tech Stack:** Python 3.14, Django 5.2, django-ninja 1.5, Postgres 16, uv, gunicorn, WhiteNoise, Docker Compose, Fly.io, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-28-backend-devops-interview-design.md`

## Global Constraints

- Python `>=3.14`; Django `>=5.2,<5.3`; django-ninja `>=1.5,<2`; psycopg[binary] `>=3.3,<4`.
- Postgres 16 exactly (`postgres:16-alpine`) — the plan relies on `GeneratedField` with a stored `tsvector`, which needs Postgres 12+.
- No new runtime dependency beyond `gunicorn` and `whitenoise`. Anything else must be justified against the standard library first.
- Ruff config is fixed: `line-length = 100`, `target-version = "py314"`, `select = ["E", "F", "I", "UP", "B"]`. All new code must pass `ruff check`.
- The virtualenv lives at `/opt/venv`, never `/app/.venv` — Compose bind-mounts the source over `/app` and would shadow it.
- The Django admin stays. Do not remove `django.contrib.admin`, `sessions`, `messages`, `auth`, or `CsrfViewMiddleware`.
- Deliberate shortcuts with a known ceiling get a `ponytail:` comment naming the ceiling and the upgrade path.
- No local `uv`, `python3.14`, `psql`, or `make` — every test and management command runs inside Docker.

---

### Task 1: Container image

**Files:**
- Create: `Dockerfile`
- Create: `gunicorn.conf.py`
- Create: `.dockerignore`
- Modify: `pyproject.toml:6-11` (dependencies)
- Modify: `core/settings.py:78` (static files)

**Interfaces:**
- Consumes: nothing.
- Produces: build targets `dev` and `runtime`; venv on `PATH` at `/opt/venv/bin`; `DJANGO_SETTINGS_MODULE=core.settings` baked in; app source at `/app`; container user `app` (uid 1000).

- [ ] **Step 1: Confirm the base image tag exists**

Run: `docker pull python:3.14-slim`
Expected: pulls successfully. If it does not exist, stop and report — every later task depends on it.

- [ ] **Step 2: Add runtime dependencies**

In `pyproject.toml`, replace the `dependencies` list:

```toml
dependencies = [
    "django>=5.2,<5.3",
    "django-ninja>=1.5,<2",
    "psycopg[binary]>=3.3,<4",
    "faker>=30",
    "gunicorn>=23",
    "whitenoise>=6.8",
]
```

- [ ] **Step 3: Add static file settings**

In `core/settings.py`, replace the line `STATIC_URL = "static/"` with:

```python
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ManifestStaticFilesStorage reads a manifest written by collectstatic. In dev
# nothing runs collectstatic, so fall back to the plain storage backend.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
```

Add WhiteNoise to `MIDDLEWARE`, immediately after `SecurityMiddleware`:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
```

- [ ] **Step 4: Add `staticfiles/` to `.gitignore`**

Append to `.gitignore`:

```
staticfiles/
.env
```

- [ ] **Step 5: Write `.dockerignore`**

```
.git
.gitignore
.venv
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
staticfiles/
docs/
*.md
.env
.github
```

- [ ] **Step 6: Write `gunicorn.conf.py`**

```python
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("WEB_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.environ.get("WEB_TIMEOUT", "30"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
```

- [ ] **Step 7: Write the `Dockerfile`**

```dockerfile
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
```

- [ ] **Step 8: Build both targets**

Run:
```bash
docker build --target dev -t bdi:dev .
docker build --target runtime -t bdi:runtime .
```
Expected: both succeed. The runtime build prints collectstatic's "N static files copied".

- [ ] **Step 9: Verify the venv is on PATH and dev deps split correctly**

Run:
```bash
docker run --rm bdi:runtime python -c "import django, gunicorn, whitenoise; print(django.get_version())"
docker run --rm bdi:dev pytest --version
docker run --rm bdi:runtime sh -c "pytest --version 2>&1 | head -1 || echo 'pytest absent (correct)'"
docker run --rm bdi:runtime id -un
```
Expected: Django version prints; `pytest --version` works on `dev`; pytest is absent on `runtime`; user is `app`.

- [ ] **Step 10: Commit**

```bash
git add Dockerfile gunicorn.conf.py .dockerignore .gitignore pyproject.toml core/settings.py
git commit -m "build: multi-stage Dockerfile with dev and runtime targets"
```

---

### Task 2: Environment-driven configuration

**Files:**
- Modify: `core/settings.py:1-62` (imports, DEBUG, SECRET_KEY, ALLOWED_HOSTS, DATABASES)
- Modify: `conftest.py` (replace the no-op)
- Create: `.env.example`
- Create: `blog/tests/test_settings.py`

**Interfaces:**
- Consumes: Task 1's `bdi:dev` image.
- Produces: `core.settings._database_config() -> dict`, `core.settings._env_bool(name: str, default: bool) -> bool`, `core.settings._env_list(name: str, default: str) -> list[str]`. Environment contract: `DATABASE_URL`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `CONN_MAX_AGE`, `DEBUG`, `SECRET_KEY`, `ALLOWED_HOSTS`.

- [ ] **Step 1: Write the failing test**

Create `blog/tests/test_settings.py`:

```python
import pytest

from core.settings import _database_config, _env_bool, _env_list


def test_database_url_is_parsed_and_credentials_decoded(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://u%40b:p%2Fw@db.internal:5433/appdb?sslmode=require",
    )
    config = _database_config()

    assert config["NAME"] == "appdb"
    assert config["USER"] == "u@b"
    assert config["PASSWORD"] == "p/w"
    assert config["HOST"] == "db.internal"
    assert config["PORT"] == "5433"
    assert config["OPTIONS"] == {"sslmode": "require"}


def test_discrete_vars_used_when_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "db")
    monkeypatch.setenv("POSTGRES_DB", "other")
    config = _database_config()

    assert config["HOST"] == "db"
    assert config["NAME"] == "other"
    assert "OPTIONS" not in config


def test_conn_max_age_always_set(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CONN_MAX_AGE", "120")
    assert _database_config()["CONN_MAX_AGE"] == 120


@pytest.mark.parametrize(
    "raw,expected",
    [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
     ("0", False), ("false", False), ("", False), ("nope", False)],
)
def test_env_bool(monkeypatch, raw, expected):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert _env_bool("SOME_FLAG", False) is expected


def test_env_list_splits_and_strips(monkeypatch):
    monkeypatch.setenv("SOME_LIST", " a , b ,, c ")
    assert _env_list("SOME_LIST") == ["a", "b", "c"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker run --rm -v "$PWD:/app" -w /app bdi:dev pytest blog/tests/test_settings.py -v`
Expected: FAIL — `ImportError: cannot import name '_database_config' from 'core.settings'`.

- [ ] **Step 3: Rewrite the top of `core/settings.py`**

Replace lines 1 through 9 (`from pathlib import Path` through `ALLOWED_HOSTS = ["*"]`) with:

```python
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = _env_bool("DEBUG", False)

SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "django-insecure-dev-only-never-use-this-in-production"

ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", "*" if DEBUG else "")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set when DEBUG is off.")
```

- [ ] **Step 4: Replace the `DATABASES` block**

Replace `core/settings.py` lines 53-62 (the whole `DATABASES = {...}` literal) with:

```python
def _database_config() -> dict:
    """Build the default database config from the environment.

    Fly injects a single DATABASE_URL; Compose and local runs use discrete
    POSTGRES_* variables. urlparse does not percent-decode credentials and Fly
    generates passwords containing reserved characters, hence unquote().
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        parts = urlparse(url)
        config = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parts.path.lstrip("/"),
            "USER": unquote(parts.username or ""),
            "PASSWORD": unquote(parts.password or ""),
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or ""),
        }
        sslmode = parse_qs(parts.query).get("sslmode")
        if sslmode:
            config["OPTIONS"] = {"sslmode": sslmode[0]}
    else:
        config = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "backend_devops_interview"),
            "USER": os.environ.get("POSTGRES_USER", "postgres"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "postgres"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    config["CONN_MAX_AGE"] = int(os.environ.get("CONN_MAX_AGE", "60"))
    return config


DATABASES = {"default": _database_config()}
```

- [ ] **Step 5: Replace `conftest.py`**

The current body is a no-op — pytest-django already calls `django.setup()`. Give the file a real job instead: supplying the dev environment the settings module now requires.

```python
import os

# settings.py refuses to import without these when DEBUG is off.
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "test-only-not-a-real-secret")
```

- [ ] **Step 6: Write `.env.example`**

```sh
# Copy to .env and adjust. Compose reads .env automatically.
DEBUG=true
SECRET_KEY=dev-only-not-a-real-secret
ALLOWED_HOSTS=*

# Either set DATABASE_URL (what Fly injects)...
# DATABASE_URL=postgres://postgres:postgres@localhost:5432/backend_devops_interview

# ...or the discrete variables. Compose sets POSTGRES_HOST=db.
POSTGRES_DB=backend_devops_interview
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
CONN_MAX_AGE=60

# Gunicorn (production image only)
WEB_CONCURRENCY=2
WEB_THREADS=4
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `docker run --rm -v "$PWD:/app" -w /app bdi:dev pytest blog/tests/test_settings.py -v`
Expected: all tests PASS. No database is required — these are pure functions.

- [ ] **Step 8: Lint**

Run: `docker run --rm -v "$PWD:/app" -w /app bdi:dev ruff check .`
Expected: no errors.

- [ ] **Step 9: Commit**

```bash
git add core/settings.py conftest.py .env.example blog/tests/test_settings.py
git commit -m "feat: drive settings from the environment"
```

---

### Task 3: Compose stack

**Files:**
- Create: `docker-compose.yml`

**Interfaces:**
- Consumes: Task 1 `dev` target, Task 2 environment contract.
- Produces: services `db` and `web`; `web` reachable at `http://localhost:8000`; test command `docker compose run --rm web pytest`.

- [ ] **Step 1: Write `docker-compose.yml`**

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: backend_devops_interview
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d backend_devops_interview"]
      interval: 5s
      timeout: 3s
      retries: 10

  web:
    build:
      context: .
      target: dev
    command: >
      sh -c "python manage.py migrate --noinput &&
             python manage.py seed --scale 0.01 &&
             python manage.py runserver 0.0.0.0:8000"
    environment:
      DEBUG: "true"
      SECRET_KEY: dev-only-not-a-real-secret
      ALLOWED_HOSTS: "*"
      POSTGRES_HOST: db
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
```

Note: `seed --scale 0.01` does not exist yet — it is added in Task 4. Until then the `seed` command ignores the flag and would fail, so Step 2 below starts the stack with an overridden command.

- [ ] **Step 2: Bring the stack up without the seed step**

Run:
```bash
docker compose run --rm --service-ports web sh -c \
  "python manage.py migrate --noinput && python manage.py runserver 0.0.0.0:8000"
```
Expected: migrations apply against the `db` service, and the dev server binds.

- [ ] **Step 3: Verify the API answers**

In a second shell, run: `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/posts`
Expected: `200`. Then stop the run with Ctrl-C.

- [ ] **Step 4: Verify the existing smoke tests pass in Compose**

Run: `docker compose run --rm web pytest -v`
Expected: the three existing tests plus Task 2's settings tests all PASS.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: docker compose stack for local development"
```

---

### Task 4: Fix the seed command

**Files:**
- Modify: `blog/management/commands/seed.py`
- Create: `blog/tests/test_seed.py`

**Interfaces:**
- Consumes: Task 3 Compose stack.
- Produces: `seed --scale <float>` (default `1.0`). At scale `s` the command writes `max(1, int(1000*s))` users, `max(5, int(50*s))` tags, `max(1, int(100_000*s))` posts, `max(1, int(500_000*s))` comments.

- [ ] **Step 1: Write the failing test**

Create `blog/tests/test_seed.py`:

```python
import pytest
from django.core.management import call_command

from blog.models import Comment, Post, Tag, User


@pytest.mark.django_db
def test_seed_scale_produces_proportional_rows():
    call_command("seed", "--scale", "0.001")

    assert User.objects.count() == 1
    assert Tag.objects.count() == 5
    assert Post.objects.count() == 100
    assert Comment.objects.count() == 500


@pytest.mark.django_db
def test_seed_is_a_noop_when_data_exists():
    User.objects.create(username="existing", email="e@example.com", display_name="E")

    call_command("seed", "--scale", "0.001")

    assert User.objects.count() == 1
    assert Post.objects.count() == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose run --rm web pytest blog/tests/test_seed.py -v`
Expected: FAIL — `unrecognized arguments: --scale`.

- [ ] **Step 3: Add the `--scale` argument and scale the constants**

In `blog/management/commands/seed.py`, add `from itertools import accumulate` to the imports, add a pool-size constant next to the others:

```python
COMMENT_POOL_SIZE = 10_000
```

Replace `add_arguments` with:

```python
    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Seed even if data exists")
        parser.add_argument(
            "--scale",
            type=float,
            default=1.0,
            help="Fraction of the full dataset to generate (default 1.0)",
        )
```

Immediately after the `if User.objects.exists() ...` guard in `handle`, insert:

```python
        scale = opts["scale"]
        n_users = max(1, int(NUM_USERS * scale))
        n_tags = max(len(HOT_SLUGS), int(NUM_TAGS * scale))
        n_posts = max(1, int(NUM_POSTS * scale))
        n_comments = max(1, int(NUM_COMMENTS * scale))
        title_pool_size = max(10, int(TITLE_POOL_SIZE * scale))
        body_pool_size = max(10, int(BODY_POOL_SIZE * scale))
        comment_pool_size = max(10, int(COMMENT_POOL_SIZE * scale))
```

Hoist the hot slugs to module level so the count above can reference them — add near the other constants and delete the local `hot_slugs = [...]` line inside `handle`:

```python
HOT_SLUGS = ["python", "django", "postgres", "devops", "sre"]
```

Then replace every hardcoded count in `handle` with its scaled variable: `range(NUM_USERS)` becomes `range(n_users)`, `range(NUM_TAGS - len(hot_slugs))` becomes `range(n_tags - len(HOT_SLUGS))`, `hot_slugs` becomes `HOT_SLUGS`, `range(TITLE_POOL_SIZE)` becomes `range(title_pool_size)`, `range(BODY_POOL_SIZE)` becomes `range(body_pool_size)`, `range(0, NUM_POSTS, BATCH)` becomes `range(0, n_posts, BATCH)`, `min(chunk_start + BATCH, NUM_POSTS)` becomes `min(chunk_start + BATCH, n_posts)`, and the two `f"Seeding {NUM_POSTS} posts..."` / `f"Seeding {NUM_COMMENTS} comments..."` messages use `n_posts` / `n_comments`.

- [ ] **Step 4: Fix the quadratic draw in the comment loop**

Replace the entire comments block (currently `seed.py:120-135`) with:

```python
        self.stdout.write(f"Seeding {n_comments} comments...")
        post_weights = _long_tail_weights(len(post_ids), top_pct=0.01, top_share=0.5)
        # random.choices() rebuilds its cumulative-weight table on every call, so
        # drawing one at a time from a 100k-element weight list costs O(posts) per
        # comment. Hoist the table and draw a whole batch per call instead.
        post_cum = list(accumulate(post_weights))
        author_cum = list(accumulate(author_weights))
        comment_pool = [
            fake.sentence(nb_words=random.randint(5, 30)) for _ in range(comment_pool_size)
        ]

        with transaction.atomic():
            for chunk_start in range(0, n_comments, BATCH):
                size = min(BATCH, n_comments - chunk_start)
                post_choices = random.choices(post_ids, cum_weights=post_cum, k=size)
                author_choices = random.choices(user_ids, cum_weights=author_cum, k=size)
                chunk = [
                    Comment(
                        post_id=pid,
                        author_id=aid,
                        body=random.choice(comment_pool),
                        created_at=_random_time(three_years_ago, now),
                    )
                    for pid, aid in zip(post_choices, author_choices, strict=True)
                ]
                Comment.objects.bulk_create(chunk, batch_size=BATCH)
```

- [ ] **Step 5: Apply the same hoist to the posts loop**

In the posts block, `author_weights` is already computed before it. Replace the per-post draw at `seed.py:84` — delete the line `author_id = random.choices(user_ids, weights=author_weights, k=1)[0]` and instead draw once per chunk. The loop body becomes:

```python
        with transaction.atomic():
            for chunk_start in range(0, n_posts, BATCH):
                size = min(BATCH, n_posts - chunk_start)
                author_choices = random.choices(user_ids, cum_weights=author_cum_posts, k=size)
                chunk = []
                for author_id in author_choices:
                    if random.random() < 0.5:
                        ts = _random_time(recency_cutoff, now)
                    else:
                        ts = _random_time(three_years_ago, now)
                    chunk.append(
                        Post(
                            author_id=author_id,
                            title=random.choice(title_pool),
                            body=random.choice(body_pool),
                            is_published=random.random() < 0.9,
                            view_count=random.randint(0, 5000),
                            created_at=ts,
                        )
                    )
                Post.objects.bulk_create(chunk, batch_size=BATCH)
```

Define `author_cum_posts` directly after `author_weights = _power_law_weights(...)`:

```python
        author_cum_posts = list(accumulate(author_weights))
```

In Step 4, reuse it rather than recomputing: replace `author_cum = list(accumulate(author_weights))` with `author_cum = author_cum_posts`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose run --rm web pytest blog/tests/test_seed.py -v`
Expected: both tests PASS.

- [ ] **Step 7: Time a 1% seed end to end**

Run:
```bash
docker compose down -v
docker compose run --rm web sh -c \
  "python manage.py migrate --noinput && time python manage.py seed --scale 0.01"
```
Expected: completes in seconds. Record the wall time — it goes in NOTES.md.

- [ ] **Step 8: Verify `docker compose up` now works as written**

Run: `docker compose up -d` then `curl -s http://localhost:8000/api/posts | head -c 200`
Expected: the stack starts, seeds at 1%, and returns JSON posts.

- [ ] **Step 9: Lint and commit**

```bash
docker run --rm -v "$PWD:/app" -w /app bdi:dev ruff check .
git add blog/management/commands/seed.py blog/tests/test_seed.py
git commit -m "perf: hoist cumulative weights out of the seed draw loop, add --scale"
```

---

### Task 5: Benchmark harness and baseline capture

**Files:**
- Create: `bench.sh`
- Create: `bench.sql`
- Create: `docs/bench/before.md`

**Interfaces:**
- Consumes: Task 4's seed.
- Produces: `./bench.sh [BASE_URL]` printing a markdown table of median response times; `bench.sql` for `EXPLAIN (ANALYZE, BUFFERS)` output.

- [ ] **Step 1: Write `bench.sh`**

```bash
#!/usr/bin/env bash
# Median wall time per endpoint. No framework: curl reports its own timing.
set -euo pipefail

BASE="${1:-http://localhost:8000}"
RUNS="${RUNS:-10}"

median() {
  sort -n | awk '{v[NR]=$1} END {print (NR%2) ? v[(NR+1)/2] : (v[NR/2]+v[NR/2+1])/2}'
}

bench() {
  local name="$1" path="$2"
  local times=()
  for _ in $(seq "$RUNS"); do
    times+=("$(curl -s -o /dev/null -w '%{time_total}' "$BASE$path")")
  done
  printf '| %-28s | %8.3f |\n' "$name" "$(printf '%s\n' "${times[@]}" | median)"
}

echo "| endpoint                     | median s |"
echo "| ---------------------------- | -------- |"
bench "GET /posts"          "/api/posts"
bench "GET /posts/search"   "/api/posts/search?q=python"
bench "GET /posts/by-tag"   "/api/posts/by-tag/python"
bench "GET /posts/1"        "/api/posts/1"
bench "GET /users/1"        "/api/users/1"
bench "GET /users/find"     "/api/users/find?email=user00001@example.com"
```

Make it executable: `chmod +x bench.sh`

- [ ] **Step 2: Write `bench.sql`**

```sql
\timing on

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post WHERE is_published ORDER BY created_at DESC LIMIT 20;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post
WHERE is_published AND (title ILIKE '%python%' OR body ILIKE '%python%')
ORDER BY created_at DESC LIMIT 20;

EXPLAIN (ANALYZE, BUFFERS)
SELECT p.* FROM blog_post p
JOIN blog_post_tags pt ON pt.post_id = p.id
JOIN blog_tag t ON t.id = pt.tag_id
WHERE t.slug = 'python' AND p.is_published
ORDER BY p.created_at DESC LIMIT 20;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_comment WHERE post_id = 1 ORDER BY created_at LIMIT 50;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_user WHERE email = 'user00001@example.com';
```

- [ ] **Step 3: Seed the full dataset**

Run:
```bash
docker compose down -v
docker compose up -d db
docker compose run --rm web python manage.py migrate --noinput
time docker compose run --rm web python manage.py seed --force
```
Expected: completes in minutes, not hours. Record the wall time.

- [ ] **Step 4: Capture baseline HTTP timings**

Run:
```bash
docker compose up -d web
./bench.sh > docs/bench/before-http.md
cat docs/bench/before-http.md
```
Expected: `/posts` and `/posts/search` are visibly slow (seconds). Note: `/posts` may time out or consume large memory — that is the finding, record it.

- [ ] **Step 5: Capture baseline query plans**

Run:
```bash
docker compose exec -T db psql -U postgres -d backend_devops_interview < bench.sql \
  > docs/bench/before-plans.txt 2>&1
grep -E "Seq Scan|Execution Time" docs/bench/before-plans.txt
```
Expected: sequential scans on `blog_post` for the list and search queries.

- [ ] **Step 6: Write `docs/bench/before.md`**

Combine the timing table and the notable plan lines into one file, with a header recording the machine, the dataset size (`SELECT count(*)` for each table), and the date.

- [ ] **Step 7: Commit**

```bash
git add bench.sh bench.sql docs/bench/
git commit -m "test: benchmark harness and baseline measurements"
```

---

### Task 6: Pagination and N+1 elimination

**Files:**
- Modify: `blog/api.py:1-61` (imports, serializers, three list endpoints)
- Modify: `core/settings.py` (pagination cap)
- Modify: `blog/tests/test_posts.py:28-34`

**Interfaces:**
- Consumes: Task 5 baseline.
- Produces: `blog.api._post_list_qs() -> QuerySet[Post]`. `/api/posts`, `/api/posts/search`, `/api/posts/by-tag/{slug}` return `{"items": [...], "count": N}` and accept `?limit=&offset=`.

- [ ] **Step 1: Write the failing tests**

Replace `test_list_posts_returns_published` in `blog/tests/test_posts.py` and add a query-count guard:

```python
@pytest.mark.django_db
def test_list_posts_returns_published(client, user):
    tag = Tag.objects.create(name="Python", slug="python")
    post = Post.objects.create(author=user, title="Hello", body="World")
    post.tags.add(tag)
    Post.objects.create(author=user, title="Draft", body="...", is_published=False)

    response = client.get("/api/posts")

    assert response.status_code == 200
    data = response.json()
    titles = [p["title"] for p in data["items"]]
    assert "Hello" in titles
    assert "Draft" not in titles
    assert data["count"] == 1


@pytest.mark.django_db
def test_list_posts_does_not_n_plus_one(client, user, django_assert_num_queries):
    for i in range(5):
        post = Post.objects.create(author=user, title=f"P{i}", body="b")
        post.tags.add(Tag.objects.create(name=f"T{i}", slug=f"t{i}"))

    # count + page (author joined) + tags prefetch = 3, regardless of row count.
    with django_assert_num_queries(3):
        client.get("/api/posts")


@pytest.mark.django_db
def test_list_posts_limit_is_capped(client, user):
    Post.objects.create(author=user, title="Hello", body="World")

    response = client.get("/api/posts?limit=99999")

    assert response.status_code == 200
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose run --rm web pytest blog/tests/test_posts.py -v`
Expected: FAIL — `TypeError: list indices must be integers` or `KeyError: 'items'`.

- [ ] **Step 3: Add the pagination cap**

Append to `core/settings.py`:

```python
# A caller must not be able to ask for the whole table back.
NINJA_PAGINATION_MAX_LIMIT = 100
NINJA_PAGINATION_PER_PAGE = 20
```

- [ ] **Step 4: Rewrite the list endpoints**

In `blog/api.py`, replace the imports and everything from `_serialize_author` through `posts_by_tag` (lines 1-61) with:

```python
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.pagination import paginate

from blog.models import Comment, Post, Tag, User
from blog.schemas import (
    CommentCreateIn,
    CommentCreateOut,
    PostCreateIn,
    PostCreateOut,
    PostDetailOut,
    PostListOut,
    UserDetailOut,
)

router = Router()


def _post_list_qs():
    """Base queryset for every list endpoint: one join for authors, one extra
    query for tags, regardless of page size."""
    return Post.objects.select_related("author").prefetch_related("tags")


@router.get("/posts", response=list[PostListOut])
@paginate
def list_posts(request):
    return _post_list_qs().filter(is_published=True).order_by("-created_at")


@router.get("/posts/by-tag/{slug}", response=list[PostListOut])
@paginate
def posts_by_tag(request, slug: str):
    tag = get_object_or_404(Tag, slug=slug)
    return _post_list_qs().filter(tags=tag, is_published=True).order_by("-created_at")
```

Leave `search_posts` alone for now — Task 8 replaces it. Move it below `posts_by_tag` and add `@paginate` plus the shared queryset:

```python
@router.get("/posts/search", response=list[PostListOut])
@paginate
def search_posts(request, q: str):
    return (
        _post_list_qs()
        .filter(Q(title__icontains=q) | Q(body__icontains=q), is_published=True)
        .order_by("-created_at")
    )
```

Keep `from django.db.models import Q` in the imports until Task 8 removes it.

The three `_serialize_*` helpers are deleted: django-ninja's `DjangoGetter` resolves a related manager to `.all()` when the schema field is a list, and a `ForeignKey` to the related instance, so `PostListOut` covers what they did by hand.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `docker compose run --rm web pytest blog/tests/test_posts.py -v`
Expected: all PASS. **If the tags field fails to serialize**, `DjangoGetter` did not resolve the manager — reinstate `_serialize_post_list` and use a plain (non-`@paginate`) list comprehension over a sliced queryset instead, then note the deviation in NOTES.md.

- [ ] **Step 6: Verify the routing order still holds**

Run: `docker compose run --rm web pytest blog/tests -v`
Expected: all PASS. `/posts/search` must still be matched before `/posts/{post_id}` — confirm with `curl -s -o /dev/null -w '%{http_code}' 'http://localhost:8000/api/posts/search?q=a'` returning `200`, not `422`.

- [ ] **Step 7: Commit**

```bash
git add blog/api.py core/settings.py blog/tests/test_posts.py
git commit -m "perf: paginate list endpoints and eliminate author/tag N+1"
```

---

### Task 7: Indexes

**Files:**
- Modify: `blog/models.py`
- Create: `blog/migrations/0002_indexes.py` (generated)

**Interfaces:**
- Consumes: Task 6.
- Produces: indexes `post_published_recent_idx`, `comment_post_created_idx`, and a b-tree on `blog_user.email`.

- [ ] **Step 1: Add the index declarations**

In `blog/models.py`, add `from django.db.models import Q` to the imports. Change `User.email` to carry an index:

```python
    email = models.CharField(max_length=255, db_index=True)
```

Add a `Meta` to `Post`:

```python
    class Meta:
        indexes = [
            # Partial: the list endpoint only ever reads published rows, so the
            # index stays smaller than a plain composite on (is_published, created_at).
            models.Index(
                fields=["-created_at"],
                condition=Q(is_published=True),
                name="post_published_recent_idx",
            ),
        ]
```

Add a `Meta` to `Comment`:

```python
    class Meta:
        indexes = [
            models.Index(fields=["post", "created_at"], name="comment_post_created_idx"),
        ]
```

- [ ] **Step 2: Generate the migration**

Run: `docker compose run --rm web python manage.py makemigrations blog --name indexes`
Expected: creates `blog/migrations/0002_indexes.py` with `AddIndex` × 2 and `AlterField` for `email`.

- [ ] **Step 3: Apply it and time it**

Run: `time docker compose run --rm web python manage.py migrate`
Expected: applies against the fully seeded database. Record the wall time.

- [ ] **Step 4: Verify the planner uses them**

Run:
```bash
docker compose exec -T db psql -U postgres -d backend_devops_interview -c \
  "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM blog_post WHERE is_published ORDER BY created_at DESC LIMIT 20;"
docker compose exec -T db psql -U postgres -d backend_devops_interview -c \
  "EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM blog_user WHERE email = 'user00001@example.com';"
```
Expected: `Index Scan using post_published_recent_idx` replaces the `Seq Scan`; the user lookup uses an index scan. If the planner still chooses a seq scan for the user table, that is legitimate at 1000 rows — note it rather than forcing it.

- [ ] **Step 5: Run the tests**

Run: `docker compose run --rm web pytest -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add blog/models.py blog/migrations/0002_indexes.py
git commit -m "perf: add partial published-recent, comment ordering, and email indexes"
```

---

### Task 8: Postgres full-text search

**Files:**
- Modify: `blog/models.py`
- Modify: `blog/api.py` (`search_posts`)
- Create: `blog/migrations/0003_search_vector.py` (generated)
- Create: `blog/tests/test_search.py`

**Interfaces:**
- Consumes: Task 7.
- Produces: `Post.search_vector` (stored generated `tsvector`), GIN index `post_search_gin`. `/api/posts/search?q=` ranks by `SearchRank`, then `-created_at`.

- [ ] **Step 1: Write the failing test**

Create `blog/tests/test_search.py`:

```python
import pytest
from django.test import Client

from blog.models import Post, User


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        username="carol", email="carol@example.com", display_name="Carol"
    )


@pytest.mark.django_db
def test_search_matches_word_in_body(client, user):
    Post.objects.create(author=user, title="Unrelated", body="A post about elephants")
    Post.objects.create(author=user, title="Also unrelated", body="Nothing to see")

    response = client.get("/api/posts/search?q=elephants")

    assert response.status_code == 200
    titles = [p["title"] for p in response.json()["items"]]
    assert titles == ["Unrelated"]


@pytest.mark.django_db
def test_search_stems_and_matches_title(client, user):
    Post.objects.create(author=user, title="Running fast", body="body")

    response = client.get("/api/posts/search?q=run")

    assert [p["title"] for p in response.json()["items"]] == ["Running fast"]


@pytest.mark.django_db
def test_search_excludes_unpublished(client, user):
    Post.objects.create(
        author=user, title="Hidden", body="elephants", is_published=False
    )

    response = client.get("/api/posts/search?q=elephants")

    assert response.json()["items"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm web pytest blog/tests/test_search.py -v`
Expected: `test_search_stems_and_matches_title` FAILS — `icontains` cannot match "run" against "Running".

- [ ] **Step 3: Add the generated column**

In `blog/models.py`, add imports:

```python
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db.models import GeneratedField
```

Add the field to `Post`, after `tags`:

```python
    # Stored generated column: Postgres keeps it in sync, no trigger and no
    # save() override. The explicit config is load-bearing — to_tsvector is only
    # IMMUTABLE in its two-argument form, and a generated column requires that.
    search_vector = GeneratedField(
        expression=SearchVector("title", weight="A", config="english")
        + SearchVector("body", weight="B", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )
```

Add the GIN index to `Post.Meta.indexes`, alongside the one from Task 7:

```python
    class Meta:
        indexes = [
            models.Index(
                fields=["-created_at"],
                condition=Q(is_published=True),
                name="post_published_recent_idx",
            ),
            GinIndex(fields=["search_vector"], name="post_search_gin"),
        ]
```

- [ ] **Step 4: Generate and apply the migration, timing it**

Run:
```bash
docker compose run --rm web python manage.py makemigrations blog --name search_vector
time docker compose run --rm web python manage.py migrate
```
Expected: applies. Adding a stored generated column rewrites `blog_post`, so this is slow on the full dataset — record the wall time.

**If the migration is rejected with "generation expression is not immutable"**, fall back: change the field to a plain `SearchVectorField(null=True)`, and add to the migration a `RunSQL` creating a `BEFORE INSERT OR UPDATE` trigger that assigns the same expression, plus a one-time `UPDATE` to backfill. Record the deviation in NOTES.md.

- [ ] **Step 5: Rewrite the search endpoint**

In `blog/api.py`, add to the imports:

```python
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F
```

and remove the now-unused `from django.db.models import Q`. Replace `search_posts` with:

```python
@router.get("/posts/search", response=list[PostListOut])
@paginate
def search_posts(request, q: str):
    query = SearchQuery(q, config="english")
    return (
        _post_list_qs()
        .filter(search_vector=query, is_published=True)
        .annotate(rank=SearchRank(F("search_vector"), query))
        .order_by("-rank", "-created_at")
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `docker compose run --rm web pytest blog/tests/test_search.py -v`
Expected: all three PASS, including the stemming test.

- [ ] **Step 7: Confirm the GIN index is used**

Run:
```bash
docker compose exec -T db psql -U postgres -d backend_devops_interview -c \
  "EXPLAIN (ANALYZE, BUFFERS) SELECT id FROM blog_post WHERE search_vector @@ plainto_tsquery('english','python') LIMIT 20;"
```
Expected: `Bitmap Index Scan on post_search_gin`.

- [ ] **Step 8: Commit**

```bash
git add blog/models.py blog/api.py blog/migrations/0003_search_vector.py blog/tests/test_search.py
git commit -m "perf: replace ILIKE search with a stored tsvector and GIN index"
```

---

### Task 9: Write-path and detail-endpoint fixes

**Files:**
- Modify: `blog/api.py` (`get_post`, `create_post`)
- Create: `blog/tests/test_post_detail.py`

**Interfaces:**
- Consumes: Task 8.
- Produces: `blog.api.MAX_EMBEDDED_COMMENTS = 50`. `GET /posts/{id}` no longer mutates `updated_at`. `POST /posts` returns 404 for an unknown tag slug.

- [ ] **Step 1: Write the failing tests**

Create `blog/tests/test_post_detail.py`:

```python
import json

import pytest
from django.test import Client

from blog.models import Comment, Post, Tag, User


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        username="dave", email="dave@example.com", display_name="Dave"
    )


@pytest.mark.django_db
def test_view_count_increments_without_touching_updated_at(client, user):
    post = Post.objects.create(author=user, title="T", body="B")
    before = Post.objects.get(pk=post.pk).updated_at

    response = client.get(f"/api/posts/{post.id}")

    assert response.json()["view_count"] == 1
    refreshed = Post.objects.get(pk=post.pk)
    assert refreshed.view_count == 1
    assert refreshed.updated_at == before


@pytest.mark.django_db
def test_post_detail_comments_are_capped(client, user):
    post = Post.objects.create(author=user, title="T", body="B")
    Comment.objects.bulk_create(
        [Comment(post=post, author=user, body=f"c{i}") for i in range(60)]
    )

    response = client.get(f"/api/posts/{post.id}")

    assert len(response.json()["comments"]) == 50


@pytest.mark.django_db
def test_post_detail_does_not_n_plus_one(client, user, django_assert_num_queries):
    post = Post.objects.create(author=user, title="T", body="B")
    Comment.objects.bulk_create(
        [Comment(post=post, author=user, body=f"c{i}") for i in range(10)]
    )

    # post+author, view_count update, tags prefetch, comments+author = 4
    with django_assert_num_queries(4):
        client.get(f"/api/posts/{post.id}")


@pytest.mark.django_db
def test_create_post_with_unknown_tag_returns_404(client, user):
    response = client.post(
        "/api/posts",
        data=json.dumps(
            {"author_id": user.id, "title": "T", "body": "B", "tag_slugs": ["nope"]}
        ),
        content_type="application/json",
    )

    assert response.status_code == 404
    assert not Post.objects.filter(title="T").exists()


@pytest.mark.django_db
def test_create_post_attaches_known_tags(client, user):
    Tag.objects.create(name="Python", slug="python")

    response = client.post(
        "/api/posts",
        data=json.dumps(
            {"author_id": user.id, "title": "T", "body": "B", "tag_slugs": ["python"]}
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert Post.objects.get(pk=response.json()["id"]).tags.count() == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose run --rm web pytest blog/tests/test_post_detail.py -v`
Expected: the `updated_at`, comment-cap, and 404 tests FAIL.

- [ ] **Step 3: Rewrite `get_post`**

In `blog/api.py`, add `from django.db import transaction` and `from ninja.errors import HttpError` to the imports, and a constant below `router = Router()`:

```python
MAX_EMBEDDED_COMMENTS = 50
```

Replace `get_post` with:

```python
@router.get("/posts/{post_id}", response=PostDetailOut)
def get_post(request, post_id: int):
    post = get_object_or_404(_post_list_qs(), id=post_id)

    # Single-column atomic bump. post.save() rewrote the whole row (body
    # included) on every read, lost concurrent increments, and silently moved
    # updated_at because of auto_now.
    # ponytail: still one write per read. Batch in memory or move the counter to
    # Redis and flush periodically if this becomes the bottleneck.
    Post.objects.filter(pk=post.pk).update(view_count=F("view_count") + 1)
    post.view_count += 1

    # ponytail: hot posts carry tens of thousands of comments; cap rather than
    # stream. A paginated /posts/{id}/comments endpoint is the upgrade.
    comments = list(
        post.comments.select_related("author").order_by("created_at")[
            :MAX_EMBEDDED_COMMENTS
        ]
    )

    return {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "author": post.author,
        "tags": post.tags.all(),
        "comments": comments,
        "view_count": post.view_count,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }
```

- [ ] **Step 4: Rewrite `create_post`**

```python
@router.post("/posts", response=PostCreateOut)
def create_post(request, payload: PostCreateIn):
    author = get_object_or_404(User, id=payload.author_id)
    with transaction.atomic():
        post = Post.objects.create(
            author=author,
            title=payload.title,
            body=payload.body,
        )
        if payload.tag_slugs:
            tags = list(Tag.objects.filter(slug__in=payload.tag_slugs))
            missing = set(payload.tag_slugs) - {tag.slug for tag in tags}
            if missing:
                raise HttpError(404, f"unknown tag slugs: {', '.join(sorted(missing))}")
            post.tags.set(tags)
    return {"id": post.id, "title": post.title}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `docker compose run --rm web pytest blog/tests/ -v`
Expected: all PASS. If `test_create_post_with_unknown_tag_returns_404` shows the post persisting, `HttpError` escaped the `atomic()` block without rolling back — confirm the `raise` is inside the `with`.

- [ ] **Step 6: Commit**

```bash
git add blog/api.py blog/tests/test_post_detail.py
git commit -m "fix: atomic view counting, capped comments, 404 on unknown tag slug"
```

---

### Task 10: After-measurements

**Files:**
- Create: `docs/bench/after.md`

**Interfaces:**
- Consumes: Tasks 5-9.
- Produces: the before/after table quoted in NOTES.md.

- [ ] **Step 1: Rebuild and restart against the full dataset**

Run:
```bash
docker compose up -d --build
docker compose run --rm web python manage.py migrate --noinput
```
Expected: no pending migrations.

- [ ] **Step 2: Capture the timings**

Run: `./bench.sh > docs/bench/after-http.md && cat docs/bench/after-http.md`
Expected: every endpoint materially faster; `/posts` and `/posts/search` by orders of magnitude.

- [ ] **Step 3: Capture the plans**

Run:
```bash
docker compose exec -T db psql -U postgres -d backend_devops_interview < bench.sql \
  > docs/bench/after-plans.txt 2>&1
grep -E "Seq Scan|Index Scan|Bitmap|Execution Time" docs/bench/after-plans.txt
```
Expected: index scans where sequential scans were.

Note: `bench.sql`'s second query still uses `ILIKE` and will still seq-scan — that is the *old* query shape, kept for comparison. Add the new shape to `bench.sql`:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post
WHERE is_published AND search_vector @@ plainto_tsquery('english', 'python')
ORDER BY created_at DESC LIMIT 20;
```

- [ ] **Step 4: Write `docs/bench/after.md`**

A single before/after table with a row per endpoint, the multiplier, and one sentence per row naming the cause (pagination, N+1, index, FTS).

- [ ] **Step 5: Commit**

```bash
git add docs/bench/ bench.sql
git commit -m "test: post-optimization measurements"
```

---

### Task 11: Production settings, health endpoints, structured logging

**Files:**
- Modify: `core/settings.py`
- Create: `core/logging.py`
- Modify: `core/urls.py`
- Create: `blog/tests/test_health.py`
- Modify: `docker-compose.yml` (healthcheck)

**Interfaces:**
- Consumes: Task 2 environment contract.
- Produces: `GET /healthz` → `{"status": "ok"}` (200, no DB). `GET /readyz` → 200 or 503 with `{"status": "unavailable"}`. `core.logging.JsonFormatter`. New env vars: `CSRF_TRUSTED_ORIGINS`, `LOG_LEVEL`.

- [ ] **Step 1: Write the failing test**

Create `blog/tests/test_health.py`:

```python
import pytest
from django.test import Client


@pytest.fixture
def client():
    return Client()


def test_healthz_needs_no_database(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readyz_reports_ok_when_database_reachable(client):
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `docker compose run --rm web pytest blog/tests/test_health.py -v`
Expected: FAIL with 404.

- [ ] **Step 3: Add the health views**

Replace `core/urls.py` entirely:

```python
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import path
from ninja import NinjaAPI

from blog.api import router as blog_router

api = NinjaAPI()
api.add_router("/", blog_router)


def healthz(request):
    """Liveness. Deliberately does not touch the database: a liveness probe that
    fails on a database blip restarts every instance during a hiccup and turns a
    degradation into an outage."""
    return JsonResponse({"status": "ok"})


def readyz(request):
    """Readiness. Gates traffic, so it does check the database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz),
    path("readyz", readyz),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
```

- [ ] **Step 4: Run to verify it passes**

Run: `docker compose run --rm web pytest blog/tests/test_health.py -v`
Expected: both PASS.

- [ ] **Step 5: Add the JSON formatter**

Create `core/logging.py`:

```python
import json
import logging


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Smaller than taking on python-json-logger, and
    the standard library has no JSON formatter of its own."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)
```

- [ ] **Step 6: Wire logging and the security settings**

Append to `core/settings.py`:

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "core.logging.JsonFormatter"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS")

if not DEBUG:
    # Fly terminates TLS at the edge. Without this header mapping,
    # SECURE_SSL_REDIRECT sees every request as plain HTTP and loops forever.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31_536_000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = "DENY"
```

- [ ] **Step 7: Verify `check --deploy` is clean**

Run:
```bash
docker build --target runtime -t bdi:runtime .
docker run --rm -e SECRET_KEY=not-a-real-secret -e ALLOWED_HOSTS=example.com \
  bdi:runtime python manage.py check --deploy
```
Expected: `System check identified no issues (0 silenced).` Any remaining warning must be fixed or explicitly justified in NOTES.md.

- [ ] **Step 8: Verify JSON logs**

Run: `docker compose up -d && docker compose logs web --tail 20`
Expected: log lines are single-line JSON objects with `ts`, `level`, `logger`, `msg`.

- [ ] **Step 9: Add the Compose healthcheck**

Add to the `web` service in `docker-compose.yml`:

```yaml
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')\""]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 40s
```

Run: `docker compose up -d && sleep 45 && docker compose ps`
Expected: `web` reports `healthy`.

- [ ] **Step 10: Commit**

```bash
git add core/settings.py core/logging.py core/urls.py blog/tests/test_health.py docker-compose.yml
git commit -m "feat: health endpoints, JSON logging, and production security settings"
```

---

### Task 12: Fly.io deployment config

**Files:**
- Create: `fly.toml`

**Interfaces:**
- Consumes: Task 1 `runtime` target, Task 11 `/readyz`.
- Produces: a validated Fly app config.

- [ ] **Step 1: Write `fly.toml`**

```toml
app = "backend-devops-interview"
primary_region = "scl"

[build]
  dockerfile = "Dockerfile"
  build-target = "runtime"

[env]
  PORT = "8000"
  DEBUG = "false"
  WEB_CONCURRENCY = "2"
  WEB_THREADS = "4"
  CONN_MAX_AGE = "60"

[deploy]
  release_command = "python manage.py migrate --noinput"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "stop"
  auto_start_machines = true
  min_machines_running = 1

  [[http_service.checks]]
    # Fly's http_service checks deregister an instance from the load balancer
    # rather than restarting it — readiness semantics, so /readyz, not /healthz.
    interval = "10s"
    timeout = "2s"
    grace_period = "15s"
    method = "GET"
    path = "/readyz"

[[vm]]
  size = "shared-cpu-1x"
  memory = "512mb"
```

- [ ] **Step 2: Validate it**

Run: `flyctl config validate --config fly.toml`
Expected: `Configuration is valid`. If it requires an existing app, run `flyctl config validate --config fly.toml --app backend-devops-interview` or fall back to `flyctl config show --config fly.toml` and record which check was used.

- [ ] **Step 3: Confirm the runtime image starts under gunicorn**

Run:
```bash
docker run --rm -d --name bdi-smoke -p 8001:8000 \
  -e SECRET_KEY=not-a-real-secret -e ALLOWED_HOSTS='*' \
  bdi:runtime
sleep 3
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8001/healthz
docker rm -f bdi-smoke
```
Expected: `200`. `/readyz` would return 503 here — there is no database attached, which is exactly what readiness should report.

- [ ] **Step 4: Document the deploy sequence in `fly.toml` comments or NOTES.md**

The commands a reader needs: `fly apps create`, `fly postgres create`, `fly postgres attach` (injects `DATABASE_URL`), `fly secrets set SECRET_KEY=... ALLOWED_HOSTS=... CSRF_TRUSTED_ORIGINS=...`, `fly deploy`.

- [ ] **Step 5: Commit**

```bash
git add fly.toml
git commit -m "feat: fly.io deployment configuration"
```

---

### Task 13: CI pipeline

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: everything above.
- Produces: jobs `test` and `build`.

- [ ] **Step 1: Write the workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: backend_devops_interview
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 10
    env:
      DEBUG: "true"
      SECRET_KEY: ci-only-not-a-real-secret
      ALLOWED_HOSTS: "*"
      POSTGRES_HOST: localhost
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
      - run: uv sync --frozen
      - run: uv run ruff check .
      - run: uv run pytest -v

  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - name: Build runtime image
        uses: docker/build-push-action@v6
        with:
          context: .
          target: runtime
          load: true
          tags: bdi:runtime
          cache-from: type=gha
          cache-to: type=gha,mode=max
      - name: Django deployment checks
        run: |
          docker run --rm \
            -e SECRET_KEY=ci-only-not-a-real-secret \
            -e ALLOWED_HOSTS=example.com \
            bdi:runtime python manage.py check --deploy
```

- [ ] **Step 2: Validate the YAML parses**

Run: `docker run --rm -v "$PWD:/w" -w /w python:3.14-slim python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`. If PyYAML is absent, run `pip install pyyaml` in the same command first.

- [ ] **Step 3: Reproduce the CI test job locally**

Run: `docker compose run --rm web sh -c "ruff check . && pytest -v"`
Expected: lint clean, all tests PASS. This is the same pair of commands CI runs.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint, test against postgres, and build the runtime image"
```

---

### Task 14: Documentation

**Files:**
- Create: `NOTES.md`
- Modify: `README.md` (running it locally)

**Interfaces:**
- Consumes: all prior tasks, especially `docs/bench/`.

- [ ] **Step 1: Rewrite the README's setup section**

Replace the "Running it locally" section. Prereqs become Docker alone. Steps become:

```sh
docker compose up
```

Document: the API at `http://localhost:8000/api/docs`, that the stack auto-seeds 1% of the dataset on first boot, the full-seed command `docker compose run --rm web python manage.py seed --force`, the test command `docker compose run --rm web pytest`, and the `{items, count}` pagination envelope with `?limit=&offset=`. Keep the endpoint table, correcting the three list rows to note pagination.

- [ ] **Step 2: Write `NOTES.md`**

Cover, in this order:

1. **What I did and why** — one section per track. Quote the real before/after table from `docs/bench/after.md`. Name the causes: unbounded result sets, author/tag N+1, missing partial index, `ILIKE` seq scan, full-row UPDATE on every read. State the measured seed improvement from Task 4 Step 7 and Task 5 Step 3.
2. **What I deliberately did not do** — authentication/authorization (README non-goal; suggested direction: API keys or JWT at the Ninja `auth=` layer, with per-route dependencies, since there is no `django.contrib.auth` user linkage in the domain model today); caching; read replicas; keyset pagination; request-ID middleware; metrics and tracing; a separate paginated comments endpoint; collapsing `_user_detail`'s two COUNT queries — that "optimization" joins two reverse foreign keys and multiplies both counts, so two indexed counts is the correct answer.
3. **The three accepted ceilings** — offset pagination, one write per read on `view_count`, comments capped at 50. Each with its upgrade path. These are marked in-code with `ponytail:` comments; `grep -rn "ponytail:" .` lists them.
4. **What I'd do next with another day** — request IDs plumbed through the JSON logs, `/posts/{id}/comments` paginated, a `pg_stat_statements` pass, load testing under concurrency rather than serial curl, and a staging deploy to Fly to confirm the release command and probes behave.
5. **AI disclosure** — the README requires it. State that Claude Code was used, and attach the transcripts.

- [ ] **Step 3: Verify the README's claim is literally true**

Run:
```bash
docker compose down -v
docker compose up -d
sleep 60
curl -s "http://localhost:8000/api/posts?limit=2" | head -c 300
```
Expected: a clean clone-to-running path with data, no manual steps. If it needs anything else, fix the README or the Compose file — whichever is wrong.

- [ ] **Step 4: Final full verification**

Run:
```bash
docker compose run --rm web sh -c "ruff check . && pytest -v"
docker run --rm -e SECRET_KEY=x -e ALLOWED_HOSTS=example.com bdi:runtime python manage.py check --deploy
flyctl config validate --config fly.toml
git status --short
```
Expected: lint clean, all tests pass, deploy checks clean, Fly config valid, working tree clean.

- [ ] **Step 5: Commit**

```bash
git add NOTES.md README.md
git commit -m "docs: NOTES.md and one-command setup instructions"
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: Compose stack → 3; env config → 2; seed → 4; measure-first → 5 and 10; pagination/N+1 → 6; indexes → 7; FTS → 8; view_count, create_post, comment cap → 9; accepted ceilings → 6, 9 (`ponytail:` comments) and 14; image/gunicorn/whitenoise → 1; health endpoints → 11; settings hardening → 11; logging → 11; fly.toml → 12; CI → 13; NOTES.md → 14. The spec's "delete the `_serialize_*` helpers" is Task 6 Step 4, with the fallback in Step 5.

**Type and name consistency.** `_post_list_qs()` is introduced in Task 6 and reused in Tasks 8 and 9. `_database_config`, `_env_bool`, `_env_list` are defined in Task 2 and reused in Task 11 (`_env_list` for `CSRF_TRUSTED_ORIGINS`). `MAX_EMBEDDED_COMMENTS` is defined and used in Task 9. `HOT_SLUGS` is hoisted in Task 4 Step 3 and used in the same step. `author_cum_posts` is defined in Task 4 Step 5 and referenced back in Step 4. Image tags `bdi:dev` and `bdi:runtime` are established in Task 1 Step 8 and used through Task 12.

**Known sequencing note.** Task 3 writes a `docker-compose.yml` whose command references `seed --scale`, which does not exist until Task 4. Task 3 Step 2 works around this with an explicit command override, and Task 4 Step 8 is the step that verifies the committed file works unmodified. This is called out in Task 3 Step 1 rather than left as a trap.
