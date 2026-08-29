\timing on

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post WHERE is_published ORDER BY created_at DESC LIMIT 20;

-- Old query shape (ILIKE), kept deliberately for before/after comparison --
-- this still seq-scans; it is not the query the app runs anymore. Term:
-- 'manage', a real whole word in the seeded corpus that matches identically
-- (21,277 published posts) under ILIKE substring search and FTS lexeme
-- search -- verified against the full seed. Earlier terms failed this bar:
-- 'qui' matched 22,309 rows via ILIKE but 0 via FTS (only ever a substring
-- inside longer words, never a standalone lexeme); 'runs' matched 0 via
-- ILIKE but ~9k via FTS (the mirror-image mistake). See docs/bench/after.md.
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post
WHERE is_published AND (title ILIKE '%manage%' OR body ILIKE '%manage%')
ORDER BY created_at DESC LIMIT 20;

-- New query shape: stored tsvector + GIN index. Same term as the ILIKE
-- query above, for an apples-to-apples comparison of query shape on an
-- identical match set (21,277 rows either way).
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post
WHERE is_published AND search_vector @@ plainto_tsquery('english', 'manage')
ORDER BY created_at DESC LIMIT 20;

EXPLAIN (ANALYZE, BUFFERS)
SELECT p.* FROM blog_post p
JOIN blog_post_tags pt ON pt.post_id = p.id
JOIN blog_tag t ON t.id = pt.tag_id
WHERE t.slug = 'python' AND p.is_published
ORDER BY p.created_at DESC LIMIT 20;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_comment WHERE post_id = 1 ORDER BY created_at LIMIT 50;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_user WHERE email = 'user00001@example.com';
