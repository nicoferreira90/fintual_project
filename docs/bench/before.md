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

**This table is the output of a single `./bench.sh` invocation** (one
`DEBUG=false docker compose up -d web`, one harness run, one file), not a
splice of separately-timed rows. An earlier draft of this baseline stitched
together a fresh `/posts/search` sample with five rows carried over
byte-for-byte from before Rulings 8/9 — that was wrong and has been replaced
by the run below. Numbers moved materially between drafts; see "What moved"
below the table.

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

**HTTP-status guard (Minor #3):** `bench()` also checks `%{http_code}` on
every completed sample, not just curl's exit code — a fast 404 would
otherwise time exactly like a fast 200. Verified by hand: pointed `bench` at
a URL guaranteed to 404 and confirmed it printed `FATAL: ... returned HTTP
404 (expected 200)` and exited 1 before any table row; pointed it back at a
real endpoint and confirmed normal operation resumed. All six endpoints in
the run below passed this check on every completed sample.

```
Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3

| endpoint                     | median s | samples   |
| ---------------------------- | -------- | --------- |
| GET /posts                   |  TIMEOUT |       0/3 |
| GET /posts/search            |   50.091 |       1/3 |
| GET /posts/by-tag            |   21.697 |       3/3 |
| GET /posts/1                 |    0.104 |     10/10 |
| GET /users/1                 |    0.013 |     10/10 |
| GET /users/find              |    0.014 |     10/10 |
```

**What moved, versus the earlier (spliced) draft of this table:**

| endpoint          | earlier draft      | this run (single invocation) |
| ----------------- | ------------------ | ----------------------------- |
| GET /posts         | TIMEOUT, 0/3        | TIMEOUT, 0/3 (unchanged)      |
| GET /posts/search  | 26.622s, 3/3        | **50.091s, 1/3** — 2 of 3 samples now hit the 60s bound |
| GET /posts/by-tag  | 50.921s, 3/3        | **21.697s, 3/3** — 2.3x faster, still 3/3 |
| GET /posts/1       | 0.168s, 10/10       | **0.104s, 10/10** |
| GET /users/1       | 0.013s, 10/10       | 0.013s, 10/10 (unchanged)     |
| GET /users/find    | 0.014s, 10/10       | 0.014s, 10/10 (unchanged)     |

This movement is real, not noise to explain away: by-tag's own
already-documented range was 21.5s-50.9s, and 21.697s sits at the bottom of
that exact range — this run just landed at the other end of the same spread
the earlier draft did. Search moved the other direction, from every sample
completing to two of three timing out. Neither shift is reconciled toward
the old numbers; both are reported as measured.

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
- **`GET /posts/search?q=qui` — treat "50.091s" as "at or beyond the 60s
  bound," not as a stable median.** Only 1 of 3 samples completed at all; a
  median computed over one data point is not a median in any statistically
  meaningful sense, it is that one sample. The other two attempts hit the
  60s `--max-time` wall exactly like `/posts` does. The honest one-line
  characterization of this endpoint, with a real 22,309-row match set and no
  pagination, is "at or past 60s, unreliably" — the printed figure is kept
  in the table (per the guard, it's a genuine completed sample, not
  fabricated) but should not be read as "search takes 50 seconds."
- **`GET /posts/by-tag/python` is slow and highly variable**: this run
  medianed 21.697s (3/3 completed), landing at the low end of the
  21.5s-50.9s range observed across earlier ad hoc single-request checks and
  the previous official run (50.921s). Same code, same data, no concurrent
  load — the spread is a few tens of percent to 2x, most likely
  GC/buffer-cache/runserver state varying between requests. The variance
  itself, not either individual number, is the finding (see Harness
  limitations).
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
  inverted "improvement" in Task 10. `bench.sql`'s query 2 (below) now uses
  the same `%qui%` term as `bench.sh` (Minor #2) and still shows the real
  defect (a genuine sequential scan) at the SQL level — that finding was
  never wrong, only the original HTTP-level number built on a zero-match
  term was misleading. Caught before it reached the final write-up; see
  Ruling 8.
- `GET /posts/1` (0.104s) and `/users/1`, `/users/find` (~0.01-0.02s) are the
  three closest to "normal" requests. `/posts/1` is slower than the two user
  lookups because `get_post` also N+1s per comment
  (`_serialize_author(c.author)` for each of post 1's 173 comments) — not
  called out in the task's known-defects list, but present and visible here.

## Query plan baseline (`bench.sql` via `psql` on the `db` container)

Full output: `docs/bench/before-plans.txt`, re-captured after Minor #2
changed `bench.sql`'s search predicate from `%python%` (0 matching rows) to
`%qui%` (matches `bench.sh`'s term, real rows). `psql < bench.sql` runs all
5 `EXPLAIN` statements in one session, so re-capturing query 2 re-captured
all 5 — queries 1, 3, 4, 5 are textually unchanged but their numbers moved
too (roughly 5-100x slower than the first capture), because this session's
buffer cache was cold (`Buffers: ... read=N`) where the first capture was
fully warm (`Buffers: shared hit=N`, no `read`). Kept as measured, not
reconciled toward the earlier warm-cache numbers — same principle as the
HTTP table above. Notable `Seq Scan` / `Execution Time` lines from the
re-capture:

```
16:               ->  Parallel Seq Scan on blog_post  (cost=0.00..8787.67 rows=37500 width=636) (actual time=0.138..133.239 rows=30024 loops=3)
23: Execution Time: 141.776 ms
41:               ->  Parallel Seq Scan on blog_post  (cost=0.00..8996.00 rows=9728 width=636) (actual time=0.054..175.399 rows=7436 loops=3)
46: Execution Time: 183.007 ms
80: Execution Time: 93.481 ms
102: Execution Time: 30.416 ms
108: Seq Scan on blog_user  (cost=0.00..29.50 rows=1 width=99) (actual time=0.015..0.611 rows=1 loops=1)
115: Execution Time: 0.627 ms
```

Per query (in `bench.sql` order; first-capture, warm-cache figure in
parens where it differs):

1. **List** (`WHERE is_published ORDER BY created_at DESC LIMIT 20`):
   `Parallel Seq Scan on blog_post` (no index on `created_at` or
   `is_published`) — 141.776ms cold-cache (18.302ms warm-cache first capture).
   Postgres parallelizes the scan and only needs a top-N heapsort for the
   `LIMIT 20`. **This is the key nuance regardless of cache state: the raw
   SQL for a paginated list is fine; `/posts`'s multi-minute wall time is
   entirely the missing `LIMIT` + N+1 at the app layer**, not a missing
   index.
2. **Search** (`title ILIKE '%qui%' OR body ILIKE '%qui%'`): `Parallel Seq
   Scan on blog_post`, `Rows Removed by Filter: 25897` per worker (3 workers
   × ~7,436 kept + ~25,897 discarded ≈ the 100,000-row table, ≈22,308 kept
   overall — matches the 22,309 count from the HTTP-level check above) —
   183.007ms. This is the confirmed defect: a leading-wildcard `ILIKE` can't
   use a btree index, full scan is the only plan Postgres has, regardless of
   how many rows match.
3. **By-tag** (join on `blog_post_tags`/`blog_tag`, `LIMIT 20`): **no seq
   scan** — index scan on `blog_tag.slug`, bitmap index scan on
   `blog_post_tags.tag_id`, index scan on `blog_post.pkey` — 93.481ms
   (31.137ms warm-cache first capture). Same pattern as query 1: the SQL
   itself is fast once `LIMIT` is applied (relative to the HTTP-level
   numbers): the 21-50s HTTP number is unpaginated N+1 over the 18,637
   matched rows, not the join.
4. **Comments for post 1** (`WHERE post_id = 1 ORDER BY created_at LIMIT
   50`): bitmap heap scan using the existing FK index on `post_id` —
   30.416ms (0.521ms warm-cache first capture, the largest relative jump —
   this one query went from all-buffer-hit to almost-all-disk-read). Index
   usage is correct either way; fine as-is.
5. **User by email**: `Seq Scan on blog_user`, `Rows Removed by Filter: 999`,
   0.627ms (0.174ms warm-cache first capture) — still fast at either figure
   because the table is 1,000 rows. Confirms "no index on `User.email`" —
   invisible at this row count, would not stay invisible at real scale.

## Harness limitations (honest disclosure)

- **`bench.sh` runs serial `curl` requests against a single-threaded
  `runserver`.** *(ponytail: this measures one request's latency at a time,
  not throughput or behavior under concurrent load, and can't be made to —
  the fix is a different tool, k6/wrk, against the gunicorn/runtime image,
  not a bigger `RUNS`.)* This measures single-request latency under whatever
  cache/GC/connection state the process happens to be in at that moment.
  The variance is not hypothetical: by-tag ranged 21.5s-50.9s across
  otherwise-identical requests (this baseline's own official run landed at
  21.697s, the low end of that range), and search went from 3/3 samples
  completing at 26.622s in an earlier draft to 1/3 completing at 50.091s in
  the run kept as the record. There is no way to distinguish "endpoint got
  slower" from "this particular sample landed in a slower moment" with only
  serial single-threaded sampling.
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

## Post-hoc correction (2026-08-29, found during Task 10)

The search term `qui` used above is a defective before/after comparison
term once search moves from `ILIKE` to full-text search: it matched 22,309
rows as an `ILIKE` *substring* (inside words like "require", "acquire",
"quick") but is not a standalone lexeme anywhere in the corpus, so a
whole-word FTS query for it returns 0 rows. The corrected term is `manage`
(21,277 rows, identically, under both `ILIKE` and FTS). The table above is
left unedited as the genuine, single-invocation record it always was — see
`docs/bench/before-http.md`'s own post-hoc correction section for the full
explanation and the corrected `manage`-term search figure (**26.992s,
3/3**, measured against this exact pre-migration code via `git checkout
d0224f2 -- blog/ core/`, restored afterward), which is the number Task 10's
after-comparison actually uses for `/posts/search`.
