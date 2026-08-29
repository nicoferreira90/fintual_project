Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3

| endpoint                     | median s | samples   |
| ---------------------------- | -------- | --------- |
| GET /posts                   |  TIMEOUT |       0/3 |
| GET /posts/search            |   50.091 |       1/3 |
| GET /posts/by-tag            |   21.697 |       3/3 |
| GET /posts/1                 |    0.104 |     10/10 |
| GET /users/1                 |    0.013 |     10/10 |
| GET /users/find              |    0.014 |     10/10 |

## Post-hoc correction (2026-08-29, found during Task 10): the search row above is not usable for a before/after comparison

The table above is left exactly as originally captured — it is a genuine,
single-invocation measurement and rewriting it would reintroduce the
"stitched-together" problem this file already went to some trouble to
avoid. But the **search term itself was found to be defective** for the
purpose it's used for in Task 10 (comparing to the after-migration FTS
search), so the `50.091` figure above must not be read as "the before
number" for search without this note.

`q=qui` matched 22,309 rows via the `ILIKE` implementation this baseline
measured — a real, substantial match set, which is why Ruling 8 (above)
picked it. What Ruling 8 didn't check: whether "qui" would still mean
anything once the search implementation changed. It doesn't — "qui" only
ever occurs as a *substring* inside longer words ("require", "acquire",
"quick"), never as a standalone word, so a whole-word full-text search for
it legitimately returns **zero** results (verified: `title ~* '\mqui\M' OR
body ~* '\mqui\M'` — a word-boundary match — returns 0 rows against the
full seed). That makes any before/after comparison built on this term
compare "a slow query over 22,309 rows" against "a fast query over 0 rows"
— not a valid speedup number in either direction.

A corrected term, `manage`, was found and verified to match **identically**
under both query shapes — 21,277 published posts via `ILIKE '%manage%'`
*and* via `search_vector @@ plainto_tsquery('english', 'manage')` — making
it a valid discriminator across the ILIKE-to-FTS migration and comparable in
magnitude to the original 22,309. `bench.sh` and `bench.sql` were updated to
use `manage` going forward (see their own comments for the full history,
including an intermediate "runs" attempt that was wrong in the mirror-image
direction — 0 matches via ILIKE, ~9k via FTS).

**Supplementary measurement**, taken against the exact pre-migration
application code (`git checkout d0224f2 -- blog/ core/`, verified restored
afterward with `git status`), same dataset, same `DEBUG=false`, same
`--max-time 60`, 3 samples:

```
GET /posts/search?q=manage   26.884577s
GET /posts/search?q=manage   26.991724s
GET /posts/search?q=manage   27.969082s
median: 26.992s, 3/3 samples, HTTP 200, 21,277-row real match set
```

This — **26.992s, 3/3** — is the correct "before" figure to compare against
Task 10's after-measurement for `/posts/search`, not the `50.091s, 1/3`
above (which used a term that no longer means anything post-FTS). One
caveat, stated rather than hidden: the database this supplementary run
executed against already carries the indexes and `search_vector` column
that this baseline's original run did not have. For the `ILIKE` code path
this is immaterial — a leading-wildcard `ILIKE` cannot use a btree index or
the GIN index regardless of what exists on the table — but it is worth
recording rather than silently assuming it is inert.
