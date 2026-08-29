## Literal `./bench.sh` output (one invocation, `DEBUG=false docker compose up -d web`)

Command: `MAX_TIME=60 RUNS=10 SLOW_RUNS=3 ./bench.sh`, run against the
unchanged 100k-post dataset, confirmed no pending migrations beforehand,
exactly as `before-http.md`'s reproduction steps specify. `bench.sh` at this
point already has both fixes applied: the repaired zero-result guard (reads
`"count"` from the paginated envelope) and the corrected search term
(`manage`, verified to match identically — 21,277 rows — under `ILIKE` and
FTS; see `bench.sh`'s own comment and `docs/bench/before-http.md`'s post-hoc
correction for why `qui` and `runs` were both wrong).

```
Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3

| endpoint                     | median s | samples   |
| ---------------------------- | -------- | --------- |
```

stderr:
```
FATAL: GET /posts returned HTTP 301 (expected 200) -- fixture is stale or endpoint broken. Fix bench.sh/reseed, don't report this timing.
```

Exit code: 1. The committed harness's own HTTP-status guard fired correctly
and refused to report a bogus timing — it just fired on a 301, not the 404
it was written to catch. **This is unrelated to the search-term fix**: it
happens on the *first* endpoint in the script (`GET /posts`, no search term
involved), before `bench.sh` even reaches `/posts/search`. Root cause: a
`SECURE_SSL_REDIRECT` regression landed in `core/settings.py` (commit
`cd98ae5`) after the baseline was captured — see `docs/bench/after.md`
("Finding 1") for detail and for the supplementary numbers captured to
still produce an honest before/after comparison without touching
`bench.sh` or application code.

## Supplementary capture (same methodology, `X-Forwarded-Proto: https` header added)

Not the output of the committed `./bench.sh` — see Finding 1 in
`docs/bench/after.md` for why the literal script cannot complete under the
mandated `DEBUG=false` condition on current `HEAD`, and for why this
workaround is faithful to the app's own declared proxy contract rather than
a bypass. Same endpoints, same params, same search term (`manage`), same
median/sample methodology and guard logic as `bench.sh`; `docker exec ...
env` was checked immediately before and after the run to confirm
`DEBUG=false` held throughout (this container was observed drifting back to
`DEBUG=true` on its own more than once during this investigation — a couple
of earlier capture attempts were caught and discarded this way).

```
Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3 (supplementary; X-Forwarded-Proto workaround)

| endpoint                     | median s | samples   |
| ---------------------------- | -------- | --------- |
| GET /posts                   |    0.045 |       3/3 |
| GET /posts/search            |    0.092 |       3/3 |
| GET /posts/by-tag            |    0.054 |       3/3 |
| GET /posts/1                 |    0.021 |     10/10 |
| GET /users/1                 |    0.015 |     10/10 |
| GET /users/find              |    0.015 |     10/10 |
```

No guard fired (search legitimately returned a non-empty, 21,277-row result
this time). `docs/bench/after.md` also reports the wider spread observed
across several repeated 15-20-sample supplementary checks of the cheap
endpoints for honesty (a single 3- or 10-sample capture on a serial harness
can land anywhere in that spread).
