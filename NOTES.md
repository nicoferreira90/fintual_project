# NOTES

## 1. What I did and why

### Developer experience

Before: install [mise](https://mise.jdx.dev/) and uv by hand, install and run
PostgreSQL 16 yourself, `createdb`, migrate, seed, run the server — several
manual, host-specific steps before you can see a response.

After: `docker compose up`. One command, one dependency (Docker), a
self-migrating, self-seeding stack.

The seed itself was the other half of "getting this running is harder than
it should be." The original seeder called
`random.choices(post_ids, weights=comment_weights, k=1)` once per comment.
CPython's `random.choices` rebuilds a cumulative-weight table (`accumulate`
over the whole weights list) on every call when you don't pass
`cum_weights` yourself — a 100,000-element `accumulate` plus a fresh list
allocation, 500,000 times, once per comment. Fixed by hoisting the
cumulative-weight table out of the loop and drawing a batch of choices per
call instead of one. Measured effect:

- Full seed (100k posts, 500k comments): **50.069s**.
- 1%-scale seed (`--scale 0.01`, the default the dev Compose stack seeds on
  first boot): **~1.4s**.

The dev stack seeds at 1% so `docker compose up` returns control in seconds;
the full dataset is one explicit command away
(`docker compose run --rm web python manage.py seed --force`) for anyone who
wants the numbers below to reproduce.

### Performance

All figures below are from `docs/bench/before.md` and `docs/bench/after.md`
— full 100k-post dataset, `DEBUG=false`, search term `manage`, `MAX_TIME=60s`.
Read the harness caveats in section 4 before treating any single number as
precise; the order of magnitude is the finding.

| endpoint | before | after | change |
| --- | --- | --- | --- |
| GET /posts | TIMEOUT (≥60s, 0/3 samples) | 0.045s | ≥1300× (lower bound) |
| GET /posts/by-tag | 21.697s | 0.060s | ~362× |
| GET /posts/search | 26.992s | 0.095s | ~284× |
| GET /posts/{id} | 0.104s | 0.023s | ~4.5× |
| GET /users/1 | 0.013s | 0.017s | no material change (noise) |
| GET /users/find | 0.014s | 0.016s | no material change (noise) |

Causes, one per defect:

- **Unbounded result sets.** `/posts` had no `LIMIT` at all — it serialized
  every published post (~90k rows) on every request. Fixed with
  django-ninja's built-in `@paginate` (`{"items": [...], "count": N}`,
  `?limit=&offset=`, capped at `NINJA_PAGINATION_MAX_LIMIT = 100` so a caller
  can't ask for the whole table back).
- **Author/tag N+1.** Each serialized post triggered a separate query for
  `post.author` and another for `post.tags.all()` — on ~90k unpaginated
  posts that's on the order of **180,000 queries for one `/posts` request**.
  `_post_list_qs()` now does `select_related("author").prefetch_related("tags")`
  once, so `/posts` runs in exactly **3 queries**: count, page, tags
  prefetch (an `IN (...)` scoped to the 20 ids on the page, not the table).
- **Missing partial index.** `ORDER BY created_at DESC LIMIT 20 WHERE
  is_published` had no supporting index, so Postgres ran a parallel seq
  scan of the whole table just to find the top 20. A partial index on
  `(is_published, created_at)` (`post_published_recent_idx`) turns that into
  a plain index walk that stops after 20 rows. Measured migration cost to
  build this index (plus the comment-ordering and user-email indexes) on the
  already-seeded 100k/500k-row tables: **3.109s**.
- **Unindexable `ILIKE`.** `title ILIKE '%q%' OR body ILIKE '%q%'` is a
  leading-wildcard pattern — no btree index can support it, so it was always
  a full sequential scan regardless of how selective the term was. Replaced
  with a stored, generated `search_vector` (`tsvector` over `title`/`body`,
  English config) plus a GIN index, queried with `SearchQuery`/`SearchRank`.
  Because the generated column requires a full rewrite of the table it's
  added to, this migration was the expensive one: **12.467s** on the seeded
  table (vs. 3.109s for the plain indexes above).
- **Full-row `UPDATE` on every read.** `get_post` called `post.save()` to
  bump `view_count`, which rewrites every column (including `body`) on
  every single detail view, and — because of `auto_now` — silently moved
  `updated_at` on a read-only endpoint. Replaced with
  `Post.objects.filter(pk=post.pk).update(view_count=F("view_count") + 1)`:
  one column, one atomic `UPDATE ... SET view_count = view_count + 1`, no
  read-modify-write race, `updated_at` untouched. This also fixed the same
  `_serialize_author(c.author)` N+1 pattern for `get_post`'s embedded
  comments (post 1 has 173 of them), which is most of that endpoint's
  ~4.5× win.

### Production readiness

- `manage.py check --deploy` returns **0 issues** with a real `SECRET_KEY`
  and `ALLOWED_HOSTS` set (`DEBUG=false`). Settings hardening: proxy-aware
  `SECURE_SSL_REDIRECT`/`SECURE_PROXY_SSL_HEADER` (Fly terminates TLS at the
  edge and forwards `X-Forwarded-Proto: https`), HSTS, secure cookies,
  `X_FRAME_OPTIONS`, `CSRF_TRUSTED_ORIGINS` from the environment.
- One multi-stage `Dockerfile` (`deps` → `prod-deps`/`dev-deps` → `app` →
  `dev`/`runtime`) serves local dev (`docker compose up`, bind-mounted
  source, `runserver`), CI (`dev` target, `pytest`), and Fly (`runtime`
  target: gunicorn, WhiteNoise-served static files behind a
  `collectstatic`-built manifest, no dev tooling in the image).
- `/healthz` (liveness) and `/readyz` (readiness — actually pings the
  database) so Compose's healthcheck and Fly's `http_service.checks` have
  something real to poll.
- JSON structured logging (`core/logging.py`'s `JsonFormatter`) so log lines
  are machine-parseable from day one instead of retrofitted later.
- `fly.toml`: one release command (`migrate --noinput`) before traffic,
  `/readyz` as the health check path (Fly's `http_service` checks deregister
  an unhealthy instance rather than restart it — readiness semantics, not
  liveness), `runtime` as the build target.
- CI (GitHub Actions): lint (`ruff check .`) and test (`pytest` against a
  real Postgres service container) on every push, plus a build of the
  `runtime` image so a broken production image fails CI, not a deploy.

## 2. What I deliberately did not do

- **Authentication / authorization.** Explicitly a non-goal in the
  assignment. If I were pointed at it next: API keys or JWT verified at
  django-ninja's `auth=` layer, applied per-route (so `POST /posts` and
  `POST /posts/{id}/comments` require it, the read endpoints don't have to).
  The one real decision that has to happen *before* any of that is code: the
  domain `User` model (`blog.User`) has no relationship to
  `django.contrib.auth.User` today — `AUTH_PASSWORD_VALIDATORS` is
  configured but nothing in this codebase creates or authenticates a
  `django.contrib.auth` user. Whoever picks this up has to decide whether
  the domain `User` becomes the authenticatable principal (custom auth
  backend / `AUTH_USER_MODEL`) or a separate identity layer sits in front of
  it and links by email. That seam decides the rest of the design; guessing
  at it wasn't worth doing for a feature the assignment says not to build.
- **Caching**, **read replicas**, **keyset pagination**, **request-ID
  middleware**, **metrics/tracing**, and a **separate paginated
  `/posts/{id}/comments` endpoint** — all real upgrades, none of them
  earned by the current dataset size or the assignment's scope. Doing 2-3
  things deep beats doing ten things shallow; these are listed again under
  "what I'd do next" and as accepted ceilings below rather than half-built
  here.
- **Collapsing `_user_detail`'s two `Count` queries into one `annotate`.**
  This one is worth spelling out because it looks like free work and isn't.
  `_user_detail` runs two separate, indexed `COUNT` queries —
  `user.posts.count()` and `user.comments.count()` — against a 1,000-row
  `blog_user` table. The tempting "optimization" is
  `User.objects.annotate(post_count=Count("posts"), comment_count=Count("comments"))`
  in one query. That's a bug, not an optimization: annotating two `Count`s
  across two different reverse foreign keys in a single query means Django
  joins `posts` and `comments` together first — every one of a user's posts
  gets cross-joined against every one of their comments before either
  `Count` runs — so both counts come back multiplied by the other
  relation's row count (a user with 3 posts and 4 comments would report 12
  for each, not 3 and 4). The fix would be `Count(..., distinct=True)` on
  both, which is correct but re-adds cost and complexity to avoid a second
  query that's already cheap and already indexed. Two queries is the right
  answer here; I left it alone on purpose.
- **Broad test coverage.** The assignment states test coverage isn't graded
  ("the smoke tests are there so you have something to wire into CI"). The
  suite stayed small and targeted rather than growing toward coverage —
  each test guards one specific behavior change made in this work (pagination
  caps, the N+1 fix's query count, FTS actually matching lexemes and not
  substrings, `view_count` leaving `updated_at` alone, the 404 on an unknown
  tag slug, the `/readyz` contract, `_env_bool`/`_env_list`/`DATABASE_URL`
  parsing, seed row counts), not a suite chasing a percentage.

## 3. Three accepted ceilings

Each is marked in code with a `ponytail:` comment naming the ceiling and the
upgrade path; `grep -rn "ponytail:" .` lists all of them (two more appear in
`bench.sh`, documenting the benchmark harness's own scope rather than a
product ceiling — see section 4).

- **Offset pagination** (`core/settings.py`, next to
  `NINJA_PAGINATION_MAX_LIMIT`). `LIMIT/OFFSET` still makes Postgres walk
  and discard every row before the requested offset — fine at 100k rows and
  the page depths anyone would actually click through, not fine at a much
  deeper offset. Upgrade: keyset pagination on `created_at`
  (`WHERE created_at < :cursor ORDER BY created_at DESC LIMIT n`), which
  django-ninja doesn't give you off the shelf the way offset pagination
  does.
- **One write per read on `view_count`** (`blog/api.py`, `get_post`). Still
  a real `UPDATE` on every single detail view, just a cheap single-column
  one instead of a full-row rewrite. Upgrade: batch increments in memory and
  flush periodically, or move the counter to Redis (`INCR`) and sync back if
  this ever becomes the bottleneck — neither was worth building against a
  counter nobody has complained about yet.
- **Post detail caps embedded comments at 50** (`blog/api.py`, `get_post`,
  `MAX_EMBEDDED_COMMENTS`). Hot posts carry tens of thousands of comments;
  capping rather than streaming means anyone past the first 50 sees nothing.
  Upgrade: a paginated `GET /posts/{id}/comments` endpoint (listed again
  under "what I'd do next").

## 4. Honest limitations

These matter more than a clean-looking write-up, so they're stated plainly
rather than smoothed over.

**`fly.toml` was never platform-validated.** `flyctl` is installed in this
environment but has no access token, so `flyctl config validate`,
`flyctl config show`, and `flyctl config show --local` all fail identically
— there's no offline validation path `flyctl` itself offers here. It's been
checked the only way available: an offline TOML structural parse
(`tomllib`) confirming it's well-formed and has the sections Fly's schema
expects. It has never been deployed, and no health probe, release command,
or Postgres attachment has ever actually run against Fly's platform. Treat
`fly.toml` as syntax-correct, not deploy-verified.

**The benchmark is a serial-latency harness, not a load test.** `bench.sh`
runs `curl` requests one at a time against a single-threaded
`manage.py runserver` (not gunicorn). It shows that one request at a time is
now fast; it says nothing about throughput or behavior under concurrent
load. `/posts/by-tag` and `/posts/search` showed real run-to-run variance
across otherwise-identical, independent runs during this work — by-tag
ranged from 21.5s to 50.9s on the unfixed code, same code, same data, no
concurrent load. The single before/after numbers in the table above should
be read against that spread and as orders of magnitude, not as precise
figures with three significant digits of meaning.

**The search benchmark term was wrong twice before it was right — worth
telling honestly, because it's the most instructive thing that happened
during this work.** The first term tried was `qui`: it matched 22,309 posts
under `ILIKE '%qui%'` because `qui` occurs as a substring inside longer
words ("require", "acquire", "quick") — but it is not the stem of any word
in the corpus, so once search moved to full-text search (which matches
whole lexemes, not substrings), the same term matched **zero** rows. Left
alone, the "after" measurement for `/posts/search` would have been timing
an empty result set and reporting it as a ~300× win over a term that, on
the new code, does no real search work at all.

This was caught, but only barely, and only because of an earlier safety net
that had itself silently stopped working. A guard had been added to
`bench.sh` specifically to fail loudly if a search term ever matched zero
rows — written for exactly this failure mode. But pagination (added for the
performance track) changed the response envelope from a bare `[]` to
`{"items": [], "count": 0}`, and the guard was still checking for the
literal string `[]`. It could never fire again, silently, and nothing
about running the benchmark told you the safety net was gone — it looked
present and active right up until the moment it needed to catch something.
The guard was repaired to check `"count": 0` in the paginated body instead,
and only then did re-running it catch the zero-match term. An intermediate
replacement term (`runs`) failed in the mirror-image direction — 0 matches
under `ILIKE`, ~9k under FTS, wrong for the *before* side instead. The final
term, `manage`, matches 21,277 published posts identically under both
`ILIKE '%manage%'` and `plainto_tsquery('english', 'manage')`, which is
what `docs/bench/before.md` and `docs/bench/after.md` both use.

The lesson generalizes past this one benchmark: a fast wrong number is more
dangerous than a slow right one, because nothing downstream questions a
number that looks plausible and arrives quickly. The guard existing wasn't
enough — it also had to be re-verified against the exact shape of data it
was checking, after an unrelated change (pagination) silently changed that
shape.

## 5. AI disclosure

Claude Code was used throughout this work — planning, implementation,
review, and this write-up. The `.superpowers/sdd/2026-08-28-backend-devops-interview/`
directory in this repo is the working record of that process: `progress.md`
is a decision ledger recording every non-obvious ruling made along the way
and why (including the search-term failure and the guard repair described
above), and the `task-*-brief.md`/`task-*-report.md`/`review-*.diff` files
are the per-task specs, implementation reports, and reviewed diffs. That
directory is the closest thing this repository has to a chat transcript and
is included as-is rather than reconstructed after the fact. This tool does
not export raw chat logs into the repository automatically; if a literal
conversation transcript is required as well, it should be exported from the
Claude Code session directly before sharing.

## 6. What I'd do next with another day

- Request IDs generated per request and plumbed through the JSON logs
  (`core/logging.py`), so a single request can be traced across log lines —
  currently absent, and the natural next step once there's more than one
  process handling requests.
- A paginated `GET /posts/{id}/comments`, replacing the accepted 50-comment
  cap on post detail with a real way to page through the rest.
- A `pg_stat_statements` pass against the seeded database to check for
  anything the four hand-picked indexes missed, rather than relying on the
  six endpoints this benchmark happened to cover.
- Real concurrent load testing — k6 or wrk against the gunicorn `runtime`
  image, not serial `curl` against `runserver` — to get an actual throughput
  number and see how the fixes hold up under concurrency rather than one
  request at a time.
- An actual staging deploy to Fly, to close the gap in section 4: run the
  release command for real, watch `/readyz` gate traffic, and confirm
  `fly.toml` works on the platform instead of just parsing.
