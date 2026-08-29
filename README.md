# Backend/DevOps Engineer Interview

A small content service: users, posts, comments, tags. Django + Ninja + Postgres.

## Running it locally

Prereq: [Docker](https://docs.docker.com/get-docker/) (with Compose). Nothing else.

```sh
docker compose up
```

That's it. On first boot the `web` service migrates the database and seeds
**1% of the dataset** (~1,000 posts, ~5,000 comments — enough to click
through the API in seconds, not the ~50s the full seed takes) before
starting the dev server. API docs at <http://localhost:8000/api/docs>.

Need the full ~100k-post / ~500k-comment dataset (e.g. to reproduce the
numbers in `docs/bench/`)?

```sh
docker compose run --rm web python manage.py seed --force
```

Run the tests:

```sh
docker compose run --rm web pytest
```

### Pagination

The three list endpoints (`/api/posts`, `/api/posts/by-tag/{slug}`,
`/api/posts/search`) return a paginated envelope, not a bare array:

```json
{"items": [ ... ], "count": 1234}
```

Control the page with query params: `?limit=&offset=` (default `limit=20`,
max `limit=100` — a caller can't ask for the whole table back in one
request).

## What the API does

| Method | Path | Description |
| ------ | ---- | ----------- |
| GET    | `/api/posts` | Published posts, newest first (paginated) |
| GET    | `/api/posts/search?q=` | Full-text search across title and body (paginated) |
| GET    | `/api/posts/by-tag/{slug}` | Posts carrying a given tag (paginated) |
| GET    | `/api/posts/{id}` | Post detail with up to 50 embedded comments |
| POST   | `/api/posts` | Create a post |
| POST   | `/api/posts/{id}/comments` | Add a comment to a post |
| GET    | `/api/users/{id}` | User profile with post and comment counts |
| GET    | `/api/users/find?email=` | Look up a user by email |

See `NOTES.md` for what changed under the hood, why, what was deliberately
left out, and the AI-assistance disclosure.

## The assignment

We want to see how you take a working prototype and turn it into something a team can develop on and operate. Pick the changes that give the strongest signal about how you'd improve this codebase if you owned it. There are three areas we care about:

1. **Developer experience.** Getting this running on a fresh laptop is harder than it should be. Make it easier.
2. **Performance.** Once the database is seeded, exercise the endpoints. Some of them are slow. Find out why and fix what you can.
3. **Production readiness.** This service is a long way from something you'd put behind a load balancer. Move it closer — pick whichever deployment target you'd reach for at work (Helm chart, ECS task def, K8s manifests, Fly, Render, plain Docker + systemd — your call).

**Depth beats breadth.** Pick 2–3 things and go deep rather than touching ten things shallowly. Write a short `NOTES.md` covering:

- What you did and why.
- What you deliberately *didn't* do.
- What you'd do next if you had another day.

## Non-goals

- **Authentication / authorization** is intentionally absent. If you want to suggest a direction in `NOTES.md`, great — but no need to implement anything.
- **Test coverage** is not what we're grading. The smoke tests are there so you have something to wire into CI.
- **Reshaping the domain model** isn't expected. Adjust it if a perf fix needs it; otherwise leave it.

## Time

Soft cap of 2–6 hours, depending on your experience and what tooling you have available (AI agents are fine — say so in `NOTES.md` and include chat transcripts). We're looking at signal, not hours.

## Deliverable

Whatever's easy for you to share: a GitHub link, a [gitfront](https://gitfront.io) link, a git bundle, even `git format-patch`. Please don't open a PR against this repo.
