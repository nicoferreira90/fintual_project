\timing on

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post WHERE is_published ORDER BY created_at DESC LIMIT 20;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM blog_post
WHERE is_published AND (title ILIKE '%qui%' OR body ILIKE '%qui%')
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
