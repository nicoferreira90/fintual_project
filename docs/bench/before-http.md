Params: MAX_TIME=60s RUNS=10 SLOW_RUNS=3

| endpoint                     | median s | samples   |
| ---------------------------- | -------- | --------- |
| GET /posts                   |  TIMEOUT |       0/3 |
| GET /posts/search            |    0.181 |     10/10 |
| GET /posts/by-tag            |   50.921 |       3/3 |
| GET /posts/1                 |    0.168 |     10/10 |
| GET /users/1                 |    0.013 |     10/10 |
| GET /users/find              |    0.014 |     10/10 |
