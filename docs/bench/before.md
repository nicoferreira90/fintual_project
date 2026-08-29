# Baseline benchmark — before

Captured 2026-08-28. This is the "before" measurement for the performance
track; Tasks 6-9 fix the endpoints below, Task 10 re-runs `bench.sh`/`bench.sql`
under the same conditions for the after-comparison.

## Machine / environment

- OS: Windows 11 Pro 10.0.26200
- CPU: 12th Gen Intel Core i5-12400 (6 cores / 12 threads)
- RAM: 15.8 GiB host; Docker Desktop VM limited to 7.654 GiB (`docker info`)
- Docker Engine 28.1.1, Docker Compose v2.35.1-desktop.1
- Postgres: `postgres:16-alpine` -> `PostgreSQL 16.13 on x86_64-pc-linux-musl`
- Django dev image (`target: dev`), `manage.py runserver` (single-threaded,
  not gunicorn) — see Harness limitations below.
- **`DEBUG=false`** for every timed run (both HTTP and SQL). `docker-
  compose.yml`'s `web.environment` reads `DEBUG: "${DEBUG:-true}"` (Ruling 9 —
  originally a hardcoded `"true"` string, which a bare shell `DEBUG=false`
  couldn't have overridden; now it follows the same `${DB_PORT:-5432}`
  pattern already used elsewhere in the file), so the documented reproduction
  is plain `DEBUG=false docker compose up -d web` — no ad hoc `-e` flag, no
  edit to the committed default (still `true` for normal dev). `SECRET_KEY`
  and `ALLOWED_HOSTS` were already set and needed no override. Task 10's
  after-run must use the same `DEBUG=false docker compose ...` invocation for
  the comparison to be honest.

## Dataset (full scale, `manage.py seed --force`, no `--scale` flag)

**Full-seed wall time: 50.069s real** (`time docker compose run --rm web
python manage.py seed --force`, fresh `docker compose down -v` + migrate
beforehand). For comparison, the previous task measured the 1%-scale seed
(`--scale 0.01`) at ~1.4s.

Row counts (`SELECT count(*)` per table, run immediately after seeding):

| table            | rows    |
| ---------------- | ------- |
| `blog_user`      | 1,000   |
| `blog_tag`       | 50      |
| `blog_post`      | 100,000 |
| `blog_post_tags` | 242,987 |
| `blog_comment`   | 500,000 |

## Fixture sanity check (before trusting any timing)

Confirmed directly against the seeded data before benchmarking:

| fixture                                | result |
| --------------------------------------- | ------ |
| `blog_post` id `1`                      | exists, `is_published = t` |
| `blog_tag` slug `python`                | exists (hot slug), 18,637 posts tagged |
| `blog_user` email `user00001@example.com` | exists (id 2, username `user00001`) |

All six benchmarked endpoints returned **HTTP 200** against these fixtures
(verified with individual `curl -w '%{http_code}'` probes before running the
full harness) — none of the "fast" numbers below are a 404 in disguise.

## HTTP timing baseline (`./bench.sh`)

Bound: `--max-time 60` per request. `/posts`, `/posts/search` and
`/posts/by-tag` used `SLOW_RUNS=3` samples instead of the default 10 — at
their unfixed, unpaginated + N+1 cost, 10 runs of `/posts` alone would be
10+ minutes with all of them timing out. `/posts/1`, `/users/1`,
`/users/find` ran the full `RUNS=10`. Sample counts are the actual counts,
recorded per row below — not implied.

**Search term (Ruling 8):** `bench.sh` searches for `qui`, not `python`. The
original `q=python` matched **zero** seeded posts (see footnote below) — a
fast 0.181s that measured "how quickly Django returns an empty list," not the
search path. `qui` is a lorem-ipsum token Faker's `text()` actually generates;
verified against the full seed it matches 22,309 published posts
(`SELECT count(*) FROM blog_post WHERE is_published AND (title ILIKE '%qui%'
OR body ILIKE '%qui%')`). The term is hardcoded in `bench.sh`, not derived at
runtime, so before/after runs search for the same thing. `bench()` also
asserts the response body isn't `[]` on any completed sample and exits 1
loudly if it is — the guard is there so a term going stale (e.g. a future
reseed changing the corpus) fails the harness instead of silently reproducing
the same trap.

```
Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3

| endpoint                     | median s | samples   |
| ---------------------------- | -------- | --------- |
| GET /posts                   |  TIMEOUT |       0/3 |
| GET /posts/search            |   26.622 |       3/3 |
| GET /posts/by-tag            |   50.921 |       3/3 |
| GET /posts/1                 |    0.168 |     10/10 |
| GET /users/1                 |    0.013 |     10/10 |
| GET /users/find              |    0.014 |     10/10 |
```

Notes on what these numbers mean, honestly:

- **`GET /posts` never completed in any of the 3 attempts** — all 3 hit the
  60s `--max-time` bound (`curl` exit 28, HTTP 000). This is the finding, not
  a missing measurement: with ~90k published posts and no pagination, the
  `post.author` + `post.tags.all()` N+1 issues on the order of 180,000
  queries for one request. A mid-flight `docker stats` snapshot (15s into an
  unbounded request) showed the web container at ~656 MiB / 7.65 GiB — so
  under `DEBUG=false` this is a wall-clock/query-count problem, not (yet) an
  OOM; the OOM risk Ruling 7 warns about is specifically the `DEBUG=true`
  `connection.queries` buffer, which this run avoided.
- **`GET /posts/by-tag/python` is slow and highly variable**: the official
  3-sample run above medianed 50.921s, but ad hoc single-request checks taken
  minutes earlier and later measured 21.8s, then 32.4s / 21.5s / 21.7s on a
  separate 3-shot probe. Same code, same data, no concurrent load — the
  spread is a few tens of percent to 2x, most likely GC/buffer-cache/runserver
  state varying between requests. The recorded median (50.921s) is the one
  `bench.sh` actually produced and is what's kept as the baseline number; the
  variance itself is a reason not to over-trust any single serial-curl sample
  (see Harness limitations).
- **`GET /posts/search?q=qui` is slow, and variable, like by-tag**: the
  recorded baseline (26.622s median, 3/3 completed) came from the run against
  the documented `DEBUG=false docker compose up -d web` invocation. A separate
  attempt shortly before that, against a `docker compose run -e DEBUG=false`
  container (the pre-Ruling-9 ad hoc override, same code, same "qui" term,
  same data), **timed out on all 3 samples** (0/3, ≥60s each) instead of
  completing. Both are kept here rather than silently picking the nicer one:
  this is the same single-threaded-`runserver` variance already noted for
  by-tag, now showing up on a second unpaginated + N+1 endpoint with a
  similar match count (22,309 vs. by-tag's 18,637). Bottom line either way:
  `/posts/search` with a real match set is slow, sometimes past the 60s
  bound — not the 0.181s the zero-match term originally suggested.
- **Footnote — the original zero-match trap (kept for the record):** the
  first cut of this baseline used `q=python` and measured 0.181s. That number
  was real but meaningless: this seeded dataset has **zero** posts whose
  title or body contain the literal text "python" (`SELECT count(*) FROM
  blog_post WHERE is_published AND (title ILIKE '%python%' OR body ILIKE
  '%python%')` → 0 rows) — Faker's random sentences never happen to contain
  it, even though `python` is a seeded tag slug used for tagging (unrelated
  to body text). The HTTP-level N+1 loop ran zero times, so the number
  measured "how fast is an empty list," not the search path — comparing it
  against a real post-fix search number would have produced a meaningless or
  inverted "improvement" in Task 10. `bench.sql`'s query 2 (below) still uses
  `%python%` and still shows the real defect (a genuine sequential scan
  discarding all 100,000 rows) at the SQL level regardless of match count —
  that finding was never wrong, only the HTTP-level number built on top of it
  was misleading. Caught before it reached the final write-up; see Ruling 8.
- `GET /posts/1` (0.168s) and `/users/1`, `/users/find` (~0.01-0.02s) are the
  three closest to "normal" requests. `/posts/1` is slower than the two user
  lookups because `get_post` also N+1s per comment
  (`_serialize_author(c.author)` for each of post 1's 173 comments) — not
  called out in the task's known-defects list, but present and visible here.

## Query plan baseline (`bench.sql` via `psql` on the `db` container)

Full output: `docs/bench/before-plans.txt`. Notable `Seq Scan` / `Execution
Time` lines:

```
16:               ->  Parallel Seq Scan on blog_post  (cost=0.00..8787.67 rows=37500 width=636) (actual time=0.010..9.460 rows=30024 loops=3)
23: Execution Time: 18.302 ms
35:         ->  Seq Scan on blog_post  (cost=0.00..9871.00 rows=18 width=636) (actual time=446.421..446.422 rows=0 loops=1)
40: Execution Time: 446.508 ms
74: Execution Time: 31.137 ms
96: Execution Time: 0.521 ms
102: Seq Scan on blog_user  (cost=0.00..29.50 rows=1 width=99) (actual time=0.012..0.160 rows=1 loops=1)
109: Execution Time: 0.174 ms
```

Per query (in `bench.sql` order):

1. **List** (`WHERE is_published ORDER BY created_at DESC LIMIT 20`):
   `Parallel Seq Scan on blog_post` (no index on `created_at` or
   `is_published`), but cheap in isolation — 18.302ms. Postgres parallelizes
   the scan and only needs a top-N heapsort for the `LIMIT 20`. **This is the
   key nuance: the raw SQL for a paginated list is fine; `/posts`'s multi-
   minute wall time is entirely the missing `LIMIT` + N+1 at the app layer**,
   not a missing index.
2. **Search** (`title ILIKE '%python%' OR body ILIKE '%python%'`):
   `Seq Scan on blog_post`, `Rows Removed by Filter: 100000` — reads and
   discards every row, 446.508ms. This is the confirmed defect: a
   leading-wildcard `ILIKE` can't use a btree index, full scan is the only
   plan Postgres has.
3. **By-tag** (join on `blog_post_tags`/`blog_tag`, `LIMIT 20`): **no seq
   scan** — index scan on `blog_tag.slug`, bitmap index scan on
   `blog_post_tags.tag_id`, index scan on `blog_post.pkey`. 31.137ms. Same
   pattern as query 1: the SQL itself is fast once `LIMIT` is applied: the
   ~21-50s HTTP number is unpaginated N+1 over the 18,637 matched rows, not
   the join.
4. **Comments for post 1** (`WHERE post_id = 1 ORDER BY created_at LIMIT
   50`): bitmap heap scan using the existing FK index on `post_id`. 0.521ms —
   fine as-is.
5. **User by email**: `Seq Scan on blog_user`, `Rows Removed by Filter: 999`,
   but only 0.174ms because the table is 1,000 rows. Confirms "no index on
   `User.email`" — invisible at this row count, would not stay invisible at
   real scale.

## Harness limitations (honest disclosure)

- `bench.sh` runs **serial `curl` requests against a single-threaded
  `runserver`**. This measures single-request latency under whatever cache/
  GC/connection state the process happens to be in at that moment — it is
  **not** a concurrency or load test, and the variance noted above for both
  by-tag (21.5s-50.9s across otherwise-identical requests) and search
  (26.622s median vs. a full 60s+ timeout in a separate attempt) is a direct
  symptom of that: there is no way to distinguish "endpoint got slower" from
  "this particular sample landed in a slower moment" with only serial single-
  threaded sampling. A real load test would need concurrent clients and a
  concurrent server (gunicorn, not `runserver`), which is out of scope here.
- The dev image (`target: dev`) runs `manage.py runserver`, not gunicorn —
  matching what Task 4's Compose stack actually runs today. Task 10 should
  use the same server for apples-to-apples comparison unless the plan calls
  for switching to the `runtime`/gunicorn image, in which case both before
  and after must use it.

## Reproduction

```bash
docker compose down -v
docker compose up -d db
docker compose run --rm web python manage.py migrate --noinput
time docker compose run --rm web python manage.py seed --force

DEBUG=false docker compose up -d web
./bench.sh > docs/bench/before-http.md

docker compose exec -T db psql -U postgres -d backend_devops_interview \
  < bench.sql > docs/bench/before-plans.txt 2>&1

docker compose stop web
```

`DEBUG=false` as a plain shell env var is enough — `docker-compose.yml`'s
`web.environment` reads `DEBUG: "${DEBUG:-true}"` (Ruling 9), the same
pattern as `${DB_PORT:-5432}` already in the file. No `-e` flag, no
`docker compose run` workaround, no edit to the committed file.
