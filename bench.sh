#!/usr/bin/env bash
# Median wall time per endpoint. No framework: curl reports its own timing.
#
# Must be run against a server started with DEBUG=false. With DEBUG=true
# Django appends every executed query to django.db.connection.queries with
# no bound; at ~180k queries for one /posts request that risks OOM and would
# measure the query logger more than the N+1 bug itself.
#
# ponytail: serial curl against a single-threaded `runserver`, one request at
# a time. This is a latency measurement, not a concurrency/load test -- it
# can't tell "this endpoint is slow" from "the server was busy with something
# else". Upgrade to k6/wrk against the gunicorn/runtime image if a
# throughput or concurrent-load number is ever actually needed.
set -euo pipefail

BASE="${1:-http://localhost:8000}"
RUNS="${RUNS:-10}"
# Ceiling per request. /posts, /posts/search and /posts/by-tag are all
# unpaginated + N+1 in their current, unfixed state and can genuinely hang;
# this bound turns that into a recorded timeout instead of a stuck harness.
MAX_TIME="${MAX_TIME:-60}"
# Slow, known-pathological endpoints get fewer samples so the whole harness
# stays runnable in minutes rather than tens of minutes. Override via env.
# ponytail: 3 samples is a small n under documented wide variance (by-tag
# measured 21.5s-50.9s across otherwise-identical requests). Good enough to
# show "this endpoint is slow", not precise enough to trust a tight median.
# Upgrade to more samples, or a real load tool, if the number itself (not
# just its order of magnitude) needs to be defensible.
SLOW_RUNS="${SLOW_RUNS:-3}"

median() {
  sort -n | awk '{v[NR]=$1} END {print (NR%2) ? v[(NR+1)/2] : (v[NR/2]+v[NR/2+1])/2}'
}

# Prints one table row. Tracks how many of the attempted samples actually
# completed inside MAX_TIME so a timeout is reported as a timeout, never as
# a fabricated or silently-missing number. Every completed sample must be
# HTTP 200 -- a 404/500 fails the run loudly rather than being silently
# timed as if it were a real answer (a fast 404 would otherwise look exactly
# like a fast success). Pass "nonempty" as the 4th arg to additionally fail
# loudly if a 200 response body is an empty JSON array `[]` -- guards
# against silently re-benchmarking "how fast is an empty result", which is
# exactly how q=python produced a meaningless 0.181s (see before.md).
bench() {
  local name="$1" path="$2" runs="${3:-$RUNS}" require_nonempty="${4:-}"
  local times=() ok=0 attempted=0 resp code t out
  out="$(mktemp)"
  for _ in $(seq "$runs"); do
    attempted=$((attempted + 1))
    if resp="$(curl -s -o "$out" -w '%{http_code} %{time_total}' --max-time "$MAX_TIME" "$BASE$path")"; then
      code="${resp%% *}"
      t="${resp#* }"
      if [ "$code" != "200" ]; then
        rm -f "$out"
        echo "FATAL: $name returned HTTP $code (expected 200) -- fixture is stale or endpoint broken. Fix bench.sh/reseed, don't report this timing." >&2
        exit 1
      fi
      times+=("$t")
      ok=$((ok + 1))
      if [ -n "$require_nonempty" ] && [ "$(cat "$out")" = "[]" ]; then
        rm -f "$out"
        echo "FATAL: $name returned zero results -- the hardcoded search term no longer matches seeded data. Fix bench.sh, don't report this timing." >&2
        exit 1
      fi
    fi
  done
  rm -f "$out"
  if [ "$ok" -eq 0 ]; then
    printf '| %-28s | %8s | %9s |\n' "$name" "TIMEOUT" "0/$attempted"
  else
    printf '| %-28s | %8.3f | %9s |\n' "$name" "$(printf '%s\n' "${times[@]}" | median)" "$ok/$attempted"
  fi
}

echo "Params: MAX_TIME=${MAX_TIME}s RUNS=${RUNS} SLOW_RUNS=${SLOW_RUNS}"
echo
echo "| endpoint                     | median s | samples   |"
echo "| ---------------------------- | -------- | --------- |"
bench "GET /posts"          "/api/posts"                                 "$SLOW_RUNS"
# "qui" is a lorem-ipsum token that Faker's text() actually generates (unlike
# "python", which never appears in body text -- see before.md's footnote).
# Verified against the full seed: 22,309 published posts match. Hardcoded,
# not derived at runtime, so before/after runs search for the same thing.
bench "GET /posts/search"   "/api/posts/search?q=qui"                    "$SLOW_RUNS" nonempty
bench "GET /posts/by-tag"   "/api/posts/by-tag/python"                   "$SLOW_RUNS"
bench "GET /posts/1"        "/api/posts/1"
bench "GET /users/1"        "/api/users/1"
bench "GET /users/find"     "/api/users/find?email=user00001@example.com"
