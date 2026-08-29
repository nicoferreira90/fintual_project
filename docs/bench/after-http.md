Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3

| endpoint                     | median s | samples   |
| ---------------------------- | -------- | --------- |
| GET /posts                   |    0.045 |       3/3 |
| GET /posts/search            |    0.095 |       3/3 |
| GET /posts/by-tag            |    0.060 |       3/3 |
| GET /posts/1                 |    0.023 |     10/10 |
| GET /users/1                 |    0.017 |     10/10 |
| GET /users/find              |    0.016 |     10/10 |
