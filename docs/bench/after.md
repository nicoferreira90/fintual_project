# After-measurements — Task 10

Captured 2026-08-29, same machine/environment as `before.md`, same
100k-post/1000-user/50-tag/242,987-post_tags/500,000-comment dataset
(verified unchanged before measuring — row counts below), same
`docker compose run --rm web python manage.py migrate --check` confirming
no pending migrations.

**Read the two findings below before the table.** Neither is a defect in
the performance track (Tasks 6-9) under test.

## Finding 1 — `SECURE_SSL_REDIRECT` needs a header the harness wasn't sending (now fixed in `bench.sh`)

`docs/bench/before-http.md` was captured against commit `d0224f2`. Current
`HEAD` includes `cd98ae5` ("feat: health endpoints, JSON logging, and
production security settings"), landed afterward, which added to
`core/settings.py`:

```python
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    ...
```

**This is correct application behavior, not a regression** — on Fly
(the deployment target) TLS terminates at the edge and every request
forwarded to the app carries `X-Forwarded-Proto: https`; without
`SECURE_SSL_REDIRECT`, a plain-HTTP request could reach the app directly
and never get upgraded. The benchmark harness, not the app, was out of
date: `bench.sh` was sending bare `curl` requests with no such header, so
under `DEBUG=false` every request 301-redirected to a `https://` URL
`runserver` doesn't speak, and `curl` (correctly) failed with
`SSL: WRONG_VERSION_NUMBER` when it tried to follow. `bench.sh`'s own
HTTP-status guard caught the resulting 301 and FATALed rather than report a
bogus timing.

**Fix, now committed in `bench.sh`**: every `curl` invocation carries
`-H "X-Forwarded-Proto: https"`, with a comment pointing at
`SECURE_PROXY_SSL_HEADER` as the reason. This is not a workaround around
`SECURE_SSL_REDIRECT` — it supplies the exact header Fly's edge already
supplies in production, so benchmarking with it is *more* faithful to the
deployed request path than benchmarking without it. `SECURE_SSL_REDIRECT`
itself is unchanged and correct; no application code was touched.

With that fix, `./bench.sh` runs to completion as one invocation, exactly
as documented in `before-http.md`'s own reproduction steps — see the table
below and `docs/bench/after-http.md` for the raw, literal capture.

**Also verified — before side unaffected.** The pre-migration application
code (checked out via `git checkout d0224f2 -- blog/ core/`, then restored
via `git checkout HEAD -- blog/ core/`, confirmed clean with `git status`
and a passing `pytest blog/tests/test_search.py` both times) has no
`SECURE_PROXY_SSL_HEADER`/`SECURE_SSL_REDIRECT` at all — grepped, zero
matches. Measured directly rather than assumed: `/api/posts/search?q=manage`
against that old code returned HTTP 200 both with and without the header,
and the timing was unaffected (25.95s/26.33s with the header present vs.
26.88s/26.99s/27.97s without, in the same run-to-run noise band this
endpoint already showed at this scale). The header is a no-op against code
that never reads it, so the same `bench.sh` — header included — is valid
for benchmarking either side of the migration.

## Finding 2 — the original benchmark search term did not survive the ILIKE→FTS migration (now corrected)

The original baseline term, `q=qui`, was chosen (Ruling 8, `before.md`)
because it is a substring of many real words ("require", "acquire",
"quick", "quiet", "unique", ...) and matched 22,309 rows under `ILIKE
'%qui%'`. Full-text search does not do substring matching — it matches
whole lexemes. `qui` is not the stem of any word in the corpus (confirmed:
`title ~* '\mqui\M' OR body ~* '\mqui\M'` — word-boundary match — returns
**0** rows), so post-migration `/api/posts/search?q=qui` legitimately
returns zero results. This also exposed a second, independent problem:
`bench.sh`'s pre-pagination zero-match guard (`[ "$(cat "$out")" = "[]" ]`)
never fires against the paginated `{"items": [], "count": 0}` envelope, so
it silently reported the empty-result timing as if it were valid.

Both problems have since been fixed directly in the repo (not by this task
alone): `bench.sh`'s guard now checks `"count"` in the paginated envelope,
and the search term has been corrected to **`manage`** — verified directly
against the full seed to match **identically**, 21,277 published posts,
under both `ILIKE '%manage%'` and
`search_vector @@ plainto_tsquery('english', 'manage')` — comparable in
magnitude to the original 22,309 and, unlike `qui`, a genuine discriminator
across the migration. (An intermediate attempt used `runs`, which fails in
the mirror-image direction: 0 matches via `ILIKE`, ~9k via FTS — wrong for
the *before* side instead.) `bench.sh`, `bench.sql`,
`docs/bench/before.md`, and `docs/bench/before-http.md` all carry this
correction and its history; see the latter's own post-hoc-correction
section for the corrected pre-migration search figure, measured against
the exact `d0224f2` application code.

## Finding 3 (was "concern") — the `DEBUG` drift is explained, not mysterious

During this investigation the web container was observed reverting from
`DEBUG=false` to `DEBUG=true` on its own between some capture attempts.
Root cause, confirmed rather than assumed: `docker-compose.yml` sets
`DEBUG: "${DEBUG:-true}"`. A `docker compose up -d web` issued *without*
`DEBUG=false` in that specific command's environment — which happened a
few times mid-investigation, e.g. while poking at the container for
unrelated checks — recreates the service against the file's default and
silently flips it back to `true`. Nothing else was involved (no rogue
background process, no Compose Watch). The fix is procedural, not code: as
now noted in `before.md`, **every** `docker compose up`/`run` in a
reproduction must carry `DEBUG=false` itself, not just the first one. Every
number kept in this document was captured with a `docker exec ... env`
check immediately before and after to confirm `DEBUG=false` held for the
whole capture; a few earlier attempts that didn't hold were discarded.

(Separately, and not a blocker: `docker-compose.yml`'s own `web.healthcheck`
polls `http://localhost:8000/healthz` from inside the container every 10s.
Under `DEBUG=false` that probe also gets 301-redirected — same root cause
as Finding 1, just from a caller this task doesn't control — so the
container shows as `unhealthy` in `docker compose ps` during a `DEBUG=false`
benchmark run. No `restart:` policy is set, so this doesn't disrupt
measurement; noting it because a stranger reproducing this will see it too
and might otherwise wonder if something is wrong.)

## Dataset / migration sanity check

| table            | rows    | matches before.md |
| ---------------- | ------- | ------------------ |
| `blog_user`      | 1,000   | yes |
| `blog_tag`       | 50      | yes |
| `blog_post`      | 100,000 | yes |
| `blog_post_tags` | 242,987 | yes |
| `blog_comment`   | 500,000 | yes |

`docker compose run --rm web python manage.py migrate --check` → exit 0, no
pending migrations. `ruff check .` → all checks passed. No application code
changes were kept — the pre-migration `blog/`/`core/` checkout used for
Finding 1 and Finding 2's before-figures was restored and verified clean
via `git status` and a passing `pytest blog/tests/test_search.py` each
time it was used.

## Reproduction

```bash
DEBUG=false docker compose up -d --build
DEBUG=false docker compose run --rm web python manage.py migrate --check

MAX_TIME=60 RUNS=10 SLOW_RUNS=3 ./bench.sh > docs/bench/after-http.md

docker compose exec -T db psql -U postgres -d backend_devops_interview \
  < bench.sql > docs/bench/after-plans.txt 2>&1
```

No header juggling required — `bench.sh` now carries
`-H "X-Forwarded-Proto: https"` itself (Finding 1), so this reproduces
cleanly at `HEAD` with no ad hoc flags, exactly like `before-http.md`'s own
reproduction steps.

## Before / after table

`Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3` for both baseline and after,
matching sample counts (no widened RUNS on the primary comparison). This
table is the literal output of one `./bench.sh` invocation — see
`docs/bench/after-http.md`. The search row's "before" figure is the
corrected `manage`-term measurement against the pre-migration code
(Finding 2), not the original `qui`-term `50.091s` figure in
`before-http.md`.

| endpoint | before median s (samples) | after median s (samples) | multiplier | cause |
| --- | --- | --- | --- | --- |
| GET /posts | TIMEOUT (0/3) | 0.045 (3/3) | **≥ ~1300x, a lower bound** — baseline never completed within the 60s bound, so there is no real baseline number to divide by exactly | pagination (`@paginate`, `LIMIT`) + eliminating the `author`/`tags` N+1 (`select_related`/`prefetch_related`) + `post_published_recent_idx` partial index turning `ORDER BY created_at DESC LIMIT 20` into an index scan instead of a full/parallel seq scan |
| GET /posts/search (`q=manage`) | 26.992 (3/3, measured against pre-migration code, 21,277-row match) | 0.095 (3/3, 21,277-row match) | **~284x** | stored generated `tsvector` (`SearchVector` on `title`/`body`) + `post_search_gin` GIN index replacing the leading-wildcard `ILIKE` (unindexable by any btree index) + the same pagination/N+1 fixes shared with the other list endpoints |
| GET /posts/by-tag | 21.697 (3/3, baseline's own documented range for this endpoint was 21.5s-50.9s across runs — read this multiplier as an order of magnitude, not a precise ratio) | 0.060 (3/3) | **~362x** | pagination + N+1 elimination (same as `/posts`); the join itself was never the bottleneck (before.md already showed index/bitmap scans here) — the win is entirely from removing the unbounded per-row `author`/`tags` N+1 |
| GET /posts/1 | 0.104 (10/10) | 0.023 (10/10) | **~4.5x** | eliminating the per-comment `_serialize_author(c.author)` N+1 (post 1 has 173 comments) via the same `select_related`/`prefetch_related` pattern, plus the atomic `F("view_count") + 1` update replacing a full-row `post.save()` |
| GET /users/1 | 0.013 (10/10) | 0.017 (10/10) | **no material change** | untouched by the performance track either way; both figures sit inside the same noise band for a serial single-request harness at the ~14-19ms floor (see "Noise floor" below) |
| GET /users/find | 0.014 (10/10) | 0.016 (10/10) | **no material change** | same as above; the new `email` index is real (see query-plan evidence) but has no visible HTTP-level effect at this scale — `blog_user` is 1,000 rows either way |

## Noise floor on the cheap endpoints (honesty requirement)

The primary-table figures for `/users/1` and `/users/find` are single
10-sample captures. To check whether the small before/after delta is real,
both were resampled 15 times each at multiple points during this session:

| endpoint | resample A | resample B | resample C (most recent) |
| --- | --- | --- | --- |
| `/users/1` | min 0.019 / med 0.021 / max 0.024 | min 0.014 / med 0.014 / max 0.021 | min 0.014 / med 0.015 / max 0.019 |
| `/users/find` | min 0.019 / med 0.020 / max 0.028 | min 0.015 / med 0.015 / max 0.020 | min 0.015 / med 0.016 / max 0.027 |
| `/posts/1` (for contrast) | — | — | min 0.020 / med 0.022 / max 0.030 |

The spread across resamples (0.014-0.028s) straddles the baseline's
0.013-0.014s on both sides — sometimes a little faster, sometimes a little
slower, depending on whatever else the container/host was doing at that
instant. **No confirmed regression** — an early working draft of this
document reported these as a "confirmed 30-46% slowdown" from one unlucky
sample; that claim did not survive resampling and was retracted rather than
left in. What is real: `/posts/1` moved from 0.104s to ~0.020-0.026s across
every sample taken, a genuine, repeatable win well outside this noise
band — so the noise on the two untouched user endpoints isn't masking
anything about the endpoints the performance track actually changed.

## Query-plan evidence (`bench.sql` via `psql`, full output: `docs/bench/after-plans.txt`)

No query against `blog_post`, `blog_comment`, or `blog_user` uses a `Seq
Scan` anymore (the sole remaining `Seq Scan` in the whole file is on
`blog_tag`, a 50-row table, where a scan is free either way). Concretely:

1. **List** (`WHERE is_published ORDER BY created_at DESC LIMIT 20`):
   `Parallel Seq Scan` (before, 141.776ms cold-cache) → `Index Scan using
   post_published_recent_idx` (after, 0.159ms). The partial index on
   `(is_published, created_at)` lets Postgres walk the index in order and
   stop after 20 rows instead of scanning the table.
2. **Search, old shape, `manage`** (`ILIKE '%manage%'`, kept in `bench.sql`
   deliberately for comparison): also now `Index Scan using
   post_published_recent_idx` (0.622ms), filtering by `ILIKE` per row after
   the index scan. Worth calling out: **the same list-pagination index
   incidentally speeds up the old `ILIKE` query shape too** — this specific
   number is not FTS doing the work, it's LIMIT-with-early-termination,
   since ~24% of published rows match "manage" so the index scan finds 20
   matches within a handful of rows. Contrast with the baseline's original
   183.007ms `Parallel Seq Scan` for the (differently-termed, same order of
   magnitude) `qui` match set, which had no such index available at the
   time.
3. **Search, new shape, `manage`** (real 21,277-row match, comparable
   magnitude to the baseline's 22,309): `Bitmap Index Scan on
   post_search_gin` → `Bitmap Heap Scan` → top-N heapsort for the `LIMIT
   20` — 56.958ms end-to-end. This is the real FTS-vs-ILIKE comparison at
   the SQL level for a genuinely large match set going through the new
   code path (as opposed to #2 above, which shows the old code path
   incidentally benefiting from an unrelated index).
4. **By-tag** (join on `blog_post_tags`/`blog_tag`, `LIMIT 20`): before.md
   already showed no seq scan here (93.481ms cold via index/bitmap scans).
   After: `Index Scan using post_published_recent_idx` feeding a `Nested
   Loop` with an `Index Only Scan` on the `post_tags` unique index —
   0.630ms, ~148x faster than the cold-cache baseline figure. The
   HTTP-level 21-50s cost was always the app-layer N+1, never this query
   (unchanged conclusion from before.md, now with a much faster base query
   too).
5. **Comments for post 1**: `Bitmap Heap Scan` via the FK index (before,
   30.416ms cold) → `Bitmap Index Scan on comment_post_created_idx` (after,
   0.704ms) — the new composite `(post_id, created_at)` index avoids a
   separate sort step for the `ORDER BY created_at LIMIT 50`.
6. **User by email**: `Seq Scan on blog_user`, `Rows Removed by Filter:
   999` (before, 0.627ms cold) → `Index Scan using
   blog_user_email_8f71103d_like` (after, 0.024ms). Confirms the new
   `email` index; as before.md noted, this was only invisible at 1,000
   rows and stays fast now regardless of scale — this is the query-level
   proof behind an index that, per the HTTP table above, made no visible
   difference at the wire yet.

## Harness limitations (carried forward from before.md, still true)

- This remains a **serial-latency harness against a single-threaded
  `runserver`** — one request at a time, no concurrency. It shows that each
  endpoint's *worst-case single request* is now fast; it says nothing about
  throughput or behavior under concurrent load. A k6/wrk run against the
  gunicorn/`runtime` image would be a different, complementary measurement,
  not a replacement for this one.
- `by-tag` and `search` showed **21.5s-50.9s** and (functionally) "at or
  past 60s" run-to-run variance in the baseline, across otherwise-identical
  requests. A single after-sample per endpoint (3 for the slow ones) should
  be read against that documented spread and against orders of magnitude,
  not treated as an exact number.
- The search row's "before" figure required checking out pre-migration
  `blog/`/`core/` code (Finding 2) — the database it ran against already
  has the new indexes and the `search_vector` column, which is immaterial
  for the `ILIKE` code path (unindexable regardless) but is recorded here
  rather than silently assumed inert.
