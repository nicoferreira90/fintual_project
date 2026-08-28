# Design: backend-devops-interview improvements

Date: 2026-08-28
Status: approved for planning

## Goal

Take the working prototype and move it toward something a team can develop on
and operate. Three tracks, one per area the README names. Depth over breadth.

Scope was fixed by three decisions:

1. One deliverable per README area (DX, performance, production readiness).
2. Deployment target: Docker Compose locally, Fly.io in production, one
   Dockerfile serving both. `flyctl` is installed on the dev machine, so
   `fly.toml` is validated locally rather than written blind.
3. Search moves to Postgres full-text search (`tsvector` + GIN), not trigram.

Explicitly kept, against an earlier proposal to cut them: the Django admin and
its supporting apps/middleware. Some people want the admin panel even on an
API-only project. Consequence: static files must be served, so WhiteNoise is a
dependency and `collectstatic` runs at image build.

## Baseline findings

Read from the code, not yet measured. Establishing measurements is the first
implementation task, and every number that reaches NOTES.md comes from there.

### Correctness / performance defects

| Location | Defect |
| --- | --- |
| `blog/api.py:44` | `/posts` returns every published row (~90k), unpaginated. |
| `blog/api.py:35-36` | N+1 on `post.author` and `post.tags.all()` in every list serializer. |
| `blog/api.py:51` | `icontains` on title/body: leading-wildcard `ILIKE`, unindexable seq scan over 100k TEXT bodies. |
| `blog/api.py:67-68` | `post.view_count += 1; post.save()` — full-row UPDATE rewriting the body on every read; non-atomic (lost updates); silently bumps `updated_at` via `auto_now`, so reading a post edits it. |
| `blog/api.py:77` | Comments on a post are unbounded and N+1 on author. Top 1% of posts hold 50% of 500k comments. |
| `blog/api.py:101` | `Tag.objects.get(slug=...)` in a loop; unknown slug raises `DoesNotExist` → HTTP 500. |
| `blog/models.py` | No index on `created_at`, none on `User.email`, none supporting comment ordering. |
| `core/settings.py:53-62` | No `CONN_MAX_AGE`: new TCP + auth handshake per request. |

### The seed is accidentally quadratic

`blog/management/commands/seed.py:125` calls
`random.choices(post_ids, weights=post_weights, k=1)` inside the 500k-comment
loop. CPython rebuilds the cumulative-weight table on every call — a
100,000-element `accumulate` plus a list allocation, 500,000 times. That is
~5x10^10 operations to draw 500k numbers, and it is why the README's "expect a
few minutes" does not match reality. The posts loop at `seed.py:84` has the
same shape over only 1,000 weights and is harmless.

### Production gaps

`DEBUG = True`, `SECRET_KEY` committed at `core/settings.py:5`,
`ALLOWED_HOSTS = ["*"]`, no `SECURE_*` settings, no WSGI server in the
dependency list, no Dockerfile, no healthcheck, no logging config, no CI,
no static file handling.

### Environment facts

`mise`, `uv`, `psql`, `make` and `just` are all absent from the dev machine;
Docker and `flyctl` are present. The documented setup path therefore cannot be
followed here at all, which is direct evidence for the DX track. It also means
no Makefile: `docker compose up` is already one command, and a Makefile would
add a tool dependency that cannot be exercised locally.

## Track 1 — Developer experience

Success criterion: `git clone` then `docker compose up` yields a seeded,
serving API on `:8000` with no host toolchain beyond Docker.

### Compose stack

`docker-compose.yml` with two services:

- `db`: `postgres:16-alpine`, named volume, `pg_isready` healthcheck.
- `web`: builds the same Dockerfile production uses, `depends_on: {db:
  {condition: service_healthy}}`, source bind-mounted for autoreload. Its
  Compose command overrides the image's gunicorn CMD with: migrate, then
  `seed --scale 0.01`, then `runserver`.

Auto-seeding at 1% is what makes "clone, `docker compose up`, working API with
data" true in under a minute. It is safe to run on every boot because `seed`
already no-ops when users exist. The full 100k/500k dataset is opt-in:

    docker compose run --rm web python manage.py seed --force

and that is the dataset the benchmarks are taken against.

### Configuration from the environment

`core/settings.py` reads `DATABASE_URL` when set (Fly provides one), otherwise
discrete `POSTGRES_*` variables with the current values as dev defaults. About
15 lines of `urllib.parse` — no `dj-database-url`, no `django-environ`.

`urlparse` does not percent-decode credentials, and Fly generates passwords
containing reserved characters, so username and password go through
`urllib.parse.unquote`. A `sslmode` query parameter maps into `OPTIONS`.

Ships with `.env.example`. `CONN_MAX_AGE` is set here (default 60).

### Seed

- Hoist `cum_weights` out of the comment loop and draw `k=BATCH`.
- Add `--scale` (float, default 1.0) so `--scale 0.01` produces ~1k posts and
  ~5k comments in seconds for day-to-day iteration.
- Wrap the comment `bulk_create` loop in `transaction.atomic()`; it is the only
  loop that is not.
- Draw comment bodies from a pool the way titles and bodies already are,
  instead of 500k `fake.sentence()` calls.

Determinism (`Faker.seed(42)`, `random.seed(42)`) is preserved.

## Track 2 — Performance

### Measure first

`bench.sh` hits each endpoint N times with
`curl -o /dev/null -s -w '%{time_total}'` and reports the median. `bench.sql`
holds the `EXPLAIN (ANALYZE, BUFFERS)` statements, run through
`docker compose exec db psql`. Both are committed and run before and after, at
full seed scale. No framework.

### Query and schema fixes

- `@paginate` (django-ninja's `LimitOffsetPagination`, already a dependency) on
  `/posts`, `/posts/search`, `/posts/by-tag/{slug}`, with
  `NINJA_PAGINATION_MAX_LIMIT = 100` in settings so a caller cannot ask for the
  whole table back. Response envelope becomes `{items, count}` — approved breaking
  change; `test_list_posts_returns_published` is updated accordingly.
- `select_related("author")` and `prefetch_related("tags")` on the list
  endpoints; `select_related("author")` on post-detail comments. With
  `@paginate` the queryset is sliced before the prefetch evaluates, so the
  prefetch covers one page, not the table.
- Prefer returning querysets and letting the Ninja schemas serialize, deleting
  `_serialize_post_list`/`_serialize_author`/`_serialize_tag`. Ninja's
  `DjangoGetter` resolves a related manager to `.all()` for a list-typed field.
  If that resolution misbehaves in practice, keep the helpers — verify, do not
  assume.
- Migration adding: a partial index on `Post(-created_at) WHERE is_published`,
  a composite index on `Comment(post, created_at)`, and `db_index=True` on
  `User.email`. Email is indexed but not made unique — that is a domain change
  the README asks us not to make.
- `view_count` becomes
  `Post.objects.filter(pk=...).update(view_count=F("view_count") + 1)`:
  atomic, single-column, and it stops the read path from touching `updated_at`.
  The in-memory instance is incremented so the response still reflects the new
  value without a refetch.
- `POST /posts` resolves tags with one `slug__in` query inside
  `transaction.atomic()` and returns 404 on an unknown slug.

`_user_detail`'s two `COUNT` queries are deliberately left alone. Collapsing
them into a single `annotate` with two `Count`s across different reverse
foreign keys produces a join fan-out that multiplies both counts. Two indexed
counts are correct and cheap; the "optimization" is a trap.

### Full-text search

`Post.search_vector` as a stored `GeneratedField`:

    GeneratedField(
        expression=SearchVector("title", weight="A", config="english")
                 + SearchVector("body", weight="B", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

plus a `GinIndex` on it. Queried with `SearchQuery(q, config="english")` and
ordered by `SearchRank` then `-created_at`.

Two risks to verify rather than assume:

1. Postgres requires a generated column's expression to be IMMUTABLE. The
   two-argument `to_tsvector(regconfig, text)` is immutable and `coalesce`/
   `setweight` are too, so the explicit `config="english"` is load-bearing.
   If the migration is nonetheless rejected, fall back to a plain
   `SearchVectorField` populated by a trigger created in the migration.
2. Adding a stored generated column rewrites the whole table. On a full seed
   this migration is slow; time it and record the number.

### Accepted ceilings

Both get a `ponytail:` comment naming the ceiling and the upgrade path, and
both are written up in NOTES.md rather than quietly shipped:

- Pagination is offset-based. Fine for shallow paging, degrades at depth;
  keyset pagination on `created_at` is the upgrade.
- View counting is still one write per read. Batching or a Redis counter
  flushed periodically is the upgrade.
- Post detail embeds at most 50 comments. A separate paginated
  `/posts/{id}/comments` endpoint is the upgrade; adding it now is scope the
  README did not ask for.

## Track 3 — Production readiness

### Image

One multi-stage `Dockerfile` serving Compose, CI and Fly:

- Builder stage on `python:3.14-slim` with uv copied in from
  `ghcr.io/astral-sh/uv:latest` (more robust than depending on a
  `uv:python3.14-*` tag existing), `uv sync --frozen --no-dev`, cache mounts.
- Runtime stage: non-root user, virtualenv copied from the builder,
  `collectstatic --noinput` at build time behind a dummy build-time
  `SECRET_KEY`, `gunicorn core.wsgi:application -c gunicorn.conf.py`.

`gunicorn.conf.py`: bind `0.0.0.0:$PORT`, workers from `WEB_CONCURRENCY`,
`accesslog = "-"`, explicit `timeout` and `graceful_timeout`.

New dependencies: `gunicorn`, `whitenoise`. WhiteNoise middleware goes
immediately after `SecurityMiddleware`, with
`CompressedManifestStaticFilesStorage` and a `STATIC_ROOT`.

### Health endpoints

Two, deliberately split:

- `/healthz` — liveness. No database call. A liveness probe that fails on a
  database blip restarts every instance during a database hiccup, turning a
  degradation into an outage.
- `/readyz` — readiness. `SELECT 1`. Gates traffic.

Fly's `http_service.checks` deregisters an instance from the load balancer
rather than restarting it, which is readiness semantics, so it points at
`/readyz`. The Compose healthcheck uses `/healthz`.

### Settings hardening

Target: `manage.py check --deploy` clean.

- `DEBUG` from env, default false.
- `SECRET_KEY` from env; raise `ImproperlyConfigured` when unset and not
  `DEBUG`, dev fallback only under `DEBUG`.
- `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` from env, comma-split.
  `CSRF_TRUSTED_ORIGINS` is required for admin login over HTTPS on Fly.
- When not `DEBUG`: `SECURE_SSL_REDIRECT`, `SECURE_HSTS_SECONDS` with
  subdomains and preload, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`,
  `X_FRAME_OPTIONS = "DENY"`, and
  `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` — without
  that last one, Fly terminates TLS at the edge and `SECURE_SSL_REDIRECT`
  causes a redirect loop.

### Logging

Structured JSON via a ~12-line `logging.Formatter` subclass in
`core/logging.py`, wired through Django's `LOGGING` dict. No dependency: there
is no stdlib JSON formatter, but the subclass is smaller than adding
`python-json-logger`. Request IDs need middleware and are deferred to NOTES.md.

### fly.toml

App config with `primary_region`, `[http_service]` on `internal_port = 8000`
with `force_https`, the `/readyz` check, a `[[vm]]` size, and
`[deploy] release_command = "python manage.py migrate --noinput"`. Validated
with `flyctl config validate`. Database is Fly Postgres, injected as
`DATABASE_URL`; secrets via `fly secrets set`.

### CI

`.github/workflows/ci.yml`, two jobs:

- `test`: `postgres:16` service container, `astral-sh/setup-uv`, `uv sync`,
  `uv run ruff check`, `uv run pytest`.
- `build`: `docker build`, then run `manage.py check --deploy` inside the built
  image with production-shaped environment variables.

## Testing

Minimal, per the README's note that coverage is not being graded — but every
piece of non-trivial logic leaves one runnable check:

- Update `test_list_posts_returns_published` for the `{items, count}` envelope.
- Search returns a seeded post by a word in its body (real Postgres, so CI).
- `GET /posts/{id}` increments `view_count` and leaves `updated_at` unchanged.
- `POST /posts` with an unknown tag slug returns 404, not 500.
- `/readyz` returns 200.
- Seed at `--scale 0.01` produces the expected row counts.

## Deliverable

`NOTES.md` covering: what changed and why; what was deliberately not done
(authentication and authorization, caching, read replicas, keyset pagination,
request IDs, metrics and tracing, a separate comments endpoint); what comes
next with another day; measured before/after numbers from `bench.sh`; and the
README-required disclosure that an AI agent was used, with transcripts.

## Out of scope

Authentication and authorization (README non-goal — a direction is described in
NOTES.md, nothing is implemented). Reshaping the domain model. Broad test
coverage.
