# After-measurements — Task 10

Captured 2026-08-29, same machine/environment as `before.md`, same
100k-post/1000-user/50-tag/242,987-post_tags/500,000-comment dataset
(verified unchanged before measuring — row counts below), same
`docker compose run --rm web python manage.py migrate --check` confirming
no pending migrations.

**Read the two findings below before the table.** Neither is a defect in
the performance track (Tasks 6-9) under test; both are consequences of
other, unrelated things (a later commit, and a benchmark-term mistake), and
both had to be corrected or worked around at the measurement layer to
produce a valid comparison.

## Finding 1 — `SECURE_SSL_REDIRECT` regression blocks the literal `bench.sh` under `DEBUG=false`

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

Correct behavior behind Fly's edge proxy (which sets
`X-Forwarded-Proto: https`), but locally there is no proxy, so every plain
`http://localhost:8000` request now looks insecure to Django and gets
301-redirected to `https://localhost:8000/...` — a scheme `runserver` never
listens on. Running the documented reproduction verbatim (`DEBUG=false
docker compose up -d web` then `./bench.sh`) FATALs immediately on the
*first* endpoint (`GET /posts`, before the script even reaches
`/posts/search`) with `HTTP 301 (expected 200)`, exit 1 — see
`docs/bench/after-http.md`. `bench.sh`'s HTTP-status guard did exactly its
job: refused to report a 301's timing as a real 200. Reproduced twice,
cleanly, with `docker exec ... env` checked immediately before and after
each run to confirm `DEBUG=false` held for the whole invocation.

(Side note on measurement hygiene: this container was also observed several
times during this session silently drifting back to `DEBUG=true` via some
container recreation this investigation did not fully root-cause. Every
number in this document was taken with an explicit `docker exec
backend-devops-interview-web-1 env | grep DEBUG` check immediately before
and after capture to rule that out; a couple of earlier capture attempts
during this session were caught and thrown out this way.)

**Given `bench.sh` and application code are both out of scope to edit for
this specific problem, the after-numbers below were captured with a
supplementary harness (`bench-workaround.sh`, kept outside the repo, not
committed) that mirrors `bench.sh`'s own `bench()`/`median()` logic and
search term** with one addition — `-H "X-Forwarded-Proto: https"` on every
`curl` call — which is precisely the header `core/settings.py`'s own
`SECURE_PROXY_SSL_HEADER` declares as the trusted proxy signal. This is not
a bypass of application behavior; it supplies, by hand, the header a real
front door already supplies in production, so the app under test still runs
its full request path (all middleware included) with `DEBUG=false`.

**This should be raised as a bug for a follow-up task**: `HEAD` cannot be
exercised over plain HTTP locally with `DEBUG=false` at all, for *any*
endpoint — a real gap in local reproduction of "production-like" behavior,
unrelated to anything Task 10 measures. Recommended fix: gate
`SECURE_SSL_REDIRECT` behind its own env var (the same `_env_bool(...)`
pattern already used for `DEBUG`), defaulting on only when a real proxy
header scheme is expected.

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

Both problems have since been fixed directly in the repo (not by this
task alone): `bench.sh`'s guard now checks `"count"` in the paginated
envelope, and the search term has been corrected to **`manage`** — verified
directly against the full seed to match **identically**, 21,277 published
posts, under both `ILIKE '%manage%'` and
`search_vector @@ plainto_tsquery('english', 'manage')` — comparable in
magnitude to the original 22,309 and, unlike `qui`, a genuine discriminator
across the migration. (An intermediate attempt used `runs`, which fails in
the mirror-image direction: 0 matches via `ILIKE`, ~9k via FTS — wrong for
the *before* side instead.) `bench.sh`, `bench.sql`,
`docs/bench/before.md`, and `docs/bench/before-http.md` all carry this
correction and its history; see the latter's own post-hoc-correction
section for the corrected pre-migration search figure, measured against
the exact `d0224f2` application code.

## Dataset / migration sanity check

| table            | rows    | matches before.md |
| ---------------- | ------- | ------------------ |
| `blog_user`      | 1,000   | yes |
| `blog_tag`       | 50      | yes |
| `blog_post`      | 100,000 | yes |
| `blog_post_tags` | 242,987 | yes |
| `blog_comment`   | 500,000 | yes |

`docker compose run --rm web python manage.py migrate --check` → exit 0, no
pending migrations. `ruff check .` → all checks passed (no application code
changes were kept by this task — the pre-migration `blog/`/`core/` checkout
used for Finding 2's before-figure was restored and verified clean via
`git status` immediately after use).

## Before / after table

`Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3` for both baseline and after,
matching sample counts (no widened RUNS on the primary comparison). "After"
column is the supplementary-harness capture (Finding 1) since the literal
`./bench.sh` cannot complete under `DEBUG=false` on current `HEAD` — see
`docs/bench/after-http.md` for both the literal FATAL and this table's raw
capture. The search row's "before" figure is the corrected `manage`-term
measurement against the pre-migration code (Finding 2), not the original
`qui`-term `50.091s` figure in `before-http.md`.

| endpoint | before median s (samples) | after median s (samples) | multiplier | cause |
| --- | --- | --- | --- | --- |
| GET /posts | TIMEOUT (0/3) | 0.045 (3/3) | **≥ ~1300x, a lower bound** — baseline never completed within the 60s bound, so there is no real baseline number to divide by exactly | pagination (`@paginate`, `LIMIT`) + eliminating the `author`/`tags` N+1 (`select_related`/`prefetch_related`) + `post_published_recent_idx` partial index turning `ORDER BY created_at DESC LIMIT 20` into an index scan instead of a full/parallel seq scan |
| GET /posts/search (`q=manage`) | 26.992 (3/3, measured against pre-migration code, 21,277-row match) | 0.092 (3/3, 21,277-row match) | **~293x** | stored generated `tsvector` (`SearchVector` on `title`/`body`) + `post_search_gin` GIN index replacing the leading-wildcard `ILIKE` (unindexable by any btree index) + the same pagination/N+1 fixes shared with the other list endpoints |
| GET /posts/by-tag | 21.697 (3/3, baseline's own documented range for this endpoint was 21.5s-50.9s across runs — read this multiplier as an order of magnitude, not a precise ratio) | 0.054 (3/3) | **~402x** | pagination + N+1 elimination (same as `/posts`); the join itself was never the bottleneck (before.md already showed index/bitmap scans here) — the win is entirely from removing the unbounded per-row `author`/`tags` N+1 |
| GET /posts/1 | 0.104 (10/10) | 0.021 (10/10) | **~5x** | eliminating the per-comment `_serialize_author(c.author)` N+1 (post 1 has 173 comments) via the same `select_related`/`prefetch_related` pattern, plus the atomic `F("view_count") + 1` update replacing a full-row `post.save()` |
| GET /users/1 | 0.013 (10/10) | 0.015 (10/10) | **no material change** | untouched by the performance track either way; both figures sit inside the same noise band for a serial single-request harness at the ~13-21ms floor (see "Noise floor" below) |
| GET /users/find | 0.014 (10/10) | 0.015 (10/10) | **no material change** | same as above; the new `email` index is real (see query-plan evidence) but has no visible HTTP-level effect at this scale — `blog_user` is 1,000 rows either way |

## Noise floor on the cheap endpoints (honesty requirement)

The primary-table figures for `/users/1` and `/users/find` are single
3-or-10-sample captures. To check whether the small before/after delta was
real, both were resampled 15-20 times each (with the header workaround) at
three separate points during this session:

| endpoint | resample 1 | resample 2 | resample 3 |
| --- | --- | --- | --- |
| `/users/1` | min 0.019 / med 0.021 / max 0.024 | min 0.014 / med 0.014 / max 0.021 | (10/10 in primary table: 0.015) |
| `/users/find` | min 0.019 / med 0.020 / max 0.028 | min 0.015 / med 0.015 / max 0.020 | (10/10 in primary table: 0.015) |

The spread across resamples (0.014-0.024s) straddles the baseline's
0.013-0.014s on both sides — sometimes reading a little faster, sometimes a
little slower, depending on whatever else the container/host was doing at
that instant. **No confirmed regression** — the earlier working draft of
this document reported these as a "confirmed 30-46% slowdown" from one
unlucky sample; that claim did not survive resampling and has been
retracted here rather than left in. What is real: `/posts/1` moved from
0.104s to ~0.021-0.026s across every sample taken, a genuine, repeatable win
that is well outside this noise band — so the noise on the two untouched
user endpoints is not masking anything about the endpoints the performance
track actually changed.

## Query-plan evidence (`bench.sql` via `psql`, full output: `docs/bench/after-plans.txt`)

No query against `blog_post`, `blog_comment`, or `blog_user` uses a `Seq
Scan` anymore (the sole remaining `Seq Scan` in the whole file is on
`blog_tag`, a 50-row table, where a scan is free either way). Concretely:

1. **List** (`WHERE is_published ORDER BY created_at DESC LIMIT 20`):
   `Parallel Seq Scan` (before, 141.776ms cold-cache) → `Index Scan using
   post_published_recent_idx` (after, 0.137ms). The partial index on
   `(is_published, created_at)` lets Postgres walk the index in order and
   stop after 20 rows instead of scanning the table.
2. **Search, old shape, `manage`** (`ILIKE '%manage%'`, kept in `bench.sql`
   deliberately for comparison): also now `Index Scan using
   post_published_recent_idx` (0.961ms), filtering by `ILIKE` per row after
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
   20` — 67.808ms end-to-end. This is the real FTS-vs-ILIKE comparison at
   the SQL level for a genuinely large match set going through the new
   code path (as opposed to #2 above, which shows the old code path
   incidentally benefiting from an unrelated index).
4. **By-tag** (join on `blog_post_tags`/`blog_tag`, `LIMIT 20`): before.md
   already showed no seq scan here (93.481ms cold via index/bitmap scans).
   After: `Index Scan using post_published_recent_idx` feeding a `Nested
   Loop` with an `Index Only Scan` on the `post_tags` unique index —
   1.204ms, ~77x faster than the cold-cache baseline figure. The HTTP-level
   21-50s cost was always the app-layer N+1, never this query (unchanged
   conclusion from before.md, now with a much faster base query too).
5. **Comments for post 1**: `Bitmap Heap Scan` via the FK index (before,
   30.416ms cold) → `Bitmap Index Scan on comment_post_created_idx` (after,
   2.086ms) — the new composite `(post_id, created_at)` index avoids a
   separate sort step for the `ORDER BY created_at LIMIT 50`.
6. **User by email**: `Seq Scan on blog_user`, `Rows Removed by Filter:
   999` (before, 0.627ms cold) → `Index Scan using
   blog_user_email_8f71103d_like` (after, 0.052ms). Confirms the new
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
- The after-numbers in the primary table are from a supplementary harness,
  not the literal committed `bench.sh` (Finding 1) — a deviation from "one
  single `./bench.sh` invocation," made necessary by an unrelated
  regression this task is not permitted to fix in application code.
  `bench.sh`'s literal output (a clean, immediate FATAL) is preserved
  unedited in `docs/bench/after-http.md` as the authoritative record of
  what the committed script actually does today.
- The search row's "before" figure required checking out pre-migration
  `blog/`/`core/` code (Finding 2) — the database it ran against already
  has the new indexes and the `search_vector` column, which is immaterial
  for the `ILIKE` code path (unindexable regardless) but is recorded here
  rather than silently assumed inert.
