# NOTES

Three tracks, one per area the assignment names. All numbers come from `docs/bench/before.md`
and `docs/bench/after.md`, reproducible with the committed `./bench.sh`.

## 1. What I did

### Developer experience

Setup went from "install mise and uv, install and run Postgres 16 yourself, `createdb`,
migrate, seed" to `docker compose up`. One command, one dependency.

The seed was the other half. It called `random.choices(post_ids, weights=..., k=1)` once per
comment, and CPython rebuilds the cumulative-weight table on every such call — a
100,000-element `accumulate` plus a list allocation, 500,000 times. Hoisting the table out of
the loop and drawing a batch per call fixed it: full seed **50.069s**, 1% seed
(`--scale 0.01`) **~1.4s**. The dev stack seeds at 1% so `docker compose up` returns in
seconds; the full dataset is `docker compose run --rm web python manage.py seed --force`.

### Performance

Full 100k-post dataset, `DEBUG=false`, search term `manage`, `MAX_TIME=60s`. The order of
magnitude is the finding, not the third digit — see section 4.

| endpoint | before | after | change |
| --- | --- | --- | --- |
| GET /posts | TIMEOUT (≥60s, 0/3 samples) | 0.045s | ≥1300× (lower bound) |
| GET /posts/by-tag | 21.697s | 0.060s | ~362× |
| GET /posts/search | 26.992s | 0.095s | ~284× |
| GET /posts/{id} | 0.104s | 0.023s | ~4.5× |
| GET /users/1, /users/find | 0.013s, 0.014s | 0.017s, 0.016s | no material change |

Five defects, one fix each:

- **No pagination.** `/posts` serialized every published post (~90k rows) per request. Now
  django-ninja's `@paginate` — `{"items": [...], "count": N}` with `?limit=&offset=`, capped
  at `NINJA_PAGINATION_MAX_LIMIT = 100`.
- **Author/tag N+1.** Each post triggered a query for `post.author` and another for
  `post.tags.all()` — roughly **180,000 queries for one `/posts` request**. `_post_list_qs()`
  now does `select_related` + `prefetch_related`, so `/posts` runs in exactly **3 queries**:
  count, page, and a tags prefetch scoped to the 20 ids on the page.
- **No supporting index.** `ORDER BY created_at DESC LIMIT 20 WHERE is_published` seq-scanned
  the table to find 20 rows. A partial index (`post_published_recent_idx`) makes it an index
  walk that stops at 20. Migration cost: **3.109s**.
- **Unindexable `ILIKE`.** A leading-wildcard pattern no btree can serve, so it always
  seq-scanned. Replaced with a stored generated `search_vector` (`tsvector` over title/body,
  English config) plus a GIN index, queried via `SearchQuery`/`SearchRank`. The generated
  column forces a full table rewrite, making this the expensive migration: **12.467s**.
- **Full-row `UPDATE` on every read.** `get_post` called `post.save()` to bump `view_count`,
  rewriting every column including `body` — and `auto_now` silently moved `updated_at` on a
  read endpoint. Now `.update(view_count=F("view_count") + 1)`: one column, atomic, no
  read-modify-write race, `updated_at` untouched. Fixing the same N+1 on embedded comment
  authors is most of that endpoint's ~4.5×.

### Production readiness

- `manage.py check --deploy` returns **0 issues**: proxy-aware `SECURE_SSL_REDIRECT` +
  `SECURE_PROXY_SSL_HEADER` (Fly terminates TLS at the edge), HSTS, secure cookies,
  `X_FRAME_OPTIONS`, env-driven `CSRF_TRUSTED_ORIGINS`.
- One multi-stage `Dockerfile` serves local dev (bind mount, `runserver`), CI (`dev` target),
  and Fly (`runtime`: gunicorn, WhiteNoise, no dev tooling).
- `/healthz` (liveness, no DB) and `/readyz` (readiness, pings the DB), split deliberately: a
  liveness probe that fails on a DB blip restarts every instance during a hiccup, turning a
  degradation into an outage.
- JSON structured logging via `core/logging.py`, no new dependency.
- `fly.toml`: `migrate --noinput` as release command, `/readyz` as the health path (Fly's
  checks deregister rather than restart — readiness semantics).
- CI: `ruff`, `pytest` against a real Postgres service, and a `runtime` image build, so a
  broken production image fails CI instead of a deploy.

## 2. What I deliberately did not do

- **Auth.** A stated non-goal. Direction: API keys or JWT at django-ninja's `auth=` layer,
  per-route. But one decision comes first — `blog.User` has no relationship to
  `django.contrib.auth`, so someone must decide whether the domain user becomes the
  authenticatable principal (`AUTH_USER_MODEL`) or a separate identity layer links by email.
  That seam determines everything downstream.
- **Caching, read replicas, keyset pagination, request IDs, metrics/tracing, and a paginated
  comments endpoint.** Real upgrades, none earned by the current dataset or scope.
- **Collapsing `_user_detail`'s two `COUNT` queries into one `annotate`.** Looks like free
  work; is a bug. Two `Count`s across different reverse foreign keys make Django join both
  relations first, so every post cross-joins every comment and both counts come back
  multiplied — 3 posts and 4 comments reports 12 for each. `distinct=True` fixes it but
  re-adds cost to avoid a second query that is already cheap and indexed.
- **Broad test coverage.** The assignment says it isn't graded. The suite stayed at 24
  targeted tests, each guarding one behavior changed here.

## 3. Accepted ceilings

Each is marked in code with a `ponytail:` comment (`grep -rn "ponytail:" .`).

- **Offset pagination.** `LIMIT/OFFSET` still walks and discards every row before the offset
  — fine at these depths, not at deep ones. Upgrade: keyset pagination on `created_at`.
- **One write per read on `view_count`.** Still an `UPDATE` per detail view, just a cheap
  single-column one. Upgrade: batch in memory, or Redis `INCR`.
- **Post detail caps embedded comments at 50.** Hot posts carry far more, so anyone past 50
  sees nothing. Upgrade: a paginated `GET /posts/{id}/comments`.

## 4. Honest limitations

**`fly.toml` was never platform-validated.** `flyctl` is installed here but has no access
token, so `config validate` and both `config show` variants fail identically. It was checked
the only way available: an offline `tomllib` parse confirming it is well-formed with the
sections Fly expects. No health probe, release command, or Postgres attachment has ever run
against Fly. Treat it as syntax-correct, not deploy-verified.

**The benchmark measures serial latency, not load.** `bench.sh` runs `curl` one request at a
time against a single-threaded `runserver`, not gunicorn. It shows one request is now fast;
it says nothing about throughput under concurrency. `/posts/by-tag` ranged from 21.5s to
50.9s across independent runs on identical unfixed code with no concurrent load. Read the
table as orders of magnitude, not three significant figures.

**The search benchmark term was wrong twice before it was right.** `qui` matched 22,309 posts
under `ILIKE` (it occurs inside "require", "quick") but is nobody's stem, so under
whole-lexeme FTS it matched **zero** — the `/posts/search` "after" number would have been
timing an empty result set and reporting a ~300× win. A replacement, `runs`, failed in the
mirror image: 0 under `ILIKE`, ~9k under FTS, wrong for the *before* side. The final term,
`manage`, matches 21,277 published posts identically under both engines.

It surfaced only because a safety net was repaired first. `bench.sh` had a guard to fail
loudly on a zero-match term, written for exactly this failure — but pagination changed the
response envelope from `[]` to `{"items": [], "count": 0}` while the guard still checked for
literal `[]`. It could never fire again, and nothing about running the benchmark revealed
that. A fast wrong number is more dangerous than a slow right one, because nothing downstream
questions a plausible number that arrives quickly; a guard existing wasn't enough, it had to
be re-verified after an unrelated change silently altered the shape it checked.

## 5. AI disclosure

Claude Code was used throughout — planning, implementation, review, and this write-up.
`.superpowers/sdd/2026-08-28-backend-devops-interview/` is the working record: `progress.md`
is a decision ledger of every non-obvious ruling and why (including the search-term failure
above), alongside per-task briefs, implementation reports, and reviewed diffs. Included
as-is. It is not a literal chat log; if one is required, export it from the Claude Code
session directly.

## 6. What I'd do next

- Request IDs plumbed through the JSON logs, so one request is traceable across lines.
- A paginated `GET /posts/{id}/comments`, retiring the 50-comment cap.
- A `pg_stat_statements` pass, rather than trusting that six benchmarked endpoints found
  everything.
- Concurrent load testing (k6 or wrk against the gunicorn image) for a real throughput number.
- A staging deploy to Fly, closing the section 4 gap: run the release command, watch
  `/readyz` gate traffic, confirm `fly.toml` works on the platform.
