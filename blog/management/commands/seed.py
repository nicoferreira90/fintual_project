import random
from datetime import timedelta
from itertools import accumulate

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from faker import Faker

from blog.models import Comment, Post, Tag, User

NUM_USERS = 1000
NUM_TAGS = 50
NUM_POSTS = 100_000
NUM_COMMENTS = 500_000
TAGS_PER_POST_AVG = 3
TITLE_POOL_SIZE = 10_000
BODY_POOL_SIZE = 10_000
COMMENT_POOL_SIZE = 10_000
BATCH = 1000
HOT_SLUGS = ["python", "django", "postgres", "devops", "sre"]


class Command(BaseCommand):
    help = "Seed the database with users, tags, posts, and comments."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Seed even if data exists")
        parser.add_argument(
            "--scale",
            type=float,
            default=1.0,
            help="Fraction of the full dataset to generate (default 1.0)",
        )

    def handle(self, *args, **opts):
        if User.objects.exists() and not opts["force"]:
            self.stdout.write("Database already has users; pass --force to seed anyway.")
            return

        scale = opts["scale"]
        n_users = max(1, int(NUM_USERS * scale))
        n_tags = max(len(HOT_SLUGS), int(NUM_TAGS * scale))
        n_posts = max(1, int(NUM_POSTS * scale))
        n_comments = max(1, int(NUM_COMMENTS * scale))
        title_pool_size = max(10, int(TITLE_POOL_SIZE * scale))
        body_pool_size = max(10, int(BODY_POOL_SIZE * scale))
        comment_pool_size = max(10, int(COMMENT_POOL_SIZE * scale))

        fake = Faker()
        Faker.seed(42)
        random.seed(42)

        now = timezone.now()
        three_years_ago = now - timedelta(days=365 * 3)

        self.stdout.write("Seeding users...")
        users = [
            User(
                username=f"user{i:05d}",
                email=f"user{i:05d}@example.com",
                display_name=fake.name(),
                bio=fake.text(max_nb_chars=200) if i % 4 == 0 else "",
                created_at=_random_time(three_years_ago, now),
            )
            for i in range(n_users)
        ]
        with transaction.atomic():
            User.objects.bulk_create(users, batch_size=BATCH)
        users = list(User.objects.all().only("id"))
        user_ids = [u.id for u in users]

        self.stdout.write("Seeding tags...")
        tag_objs = [Tag(name=s.title(), slug=s, created_at=now) for s in HOT_SLUGS]
        for _ in range(n_tags - len(HOT_SLUGS)):
            word = fake.unique.word()
            tag_objs.append(Tag(name=word.title(), slug=slugify(word), created_at=now))
        with transaction.atomic():
            Tag.objects.bulk_create(tag_objs, batch_size=BATCH)
        tags = list(Tag.objects.all().only("id", "slug"))
        hot_tag_ids = [t.id for t in tags if t.slug in HOT_SLUGS]
        cold_tag_ids = [t.id for t in tags if t.slug not in HOT_SLUGS]

        title_pool = [fake.sentence(nb_words=8).rstrip(".") for _ in range(title_pool_size)]
        body_pool = [fake.text(max_nb_chars=600) for _ in range(body_pool_size)]

        author_weights = _power_law_weights(len(user_ids), top_n=10, top_share=0.3)
        author_cum_posts = list(accumulate(author_weights))

        self.stdout.write(f"Seeding {n_posts} posts...")
        recent_days = 180
        recency_cutoff = now - timedelta(days=recent_days)
        with transaction.atomic():
            for chunk_start in range(0, n_posts, BATCH):
                size = min(BATCH, n_posts - chunk_start)
                author_choices = random.choices(user_ids, cum_weights=author_cum_posts, k=size)
                chunk = []
                for author_id in author_choices:
                    if random.random() < 0.5:
                        ts = _random_time(recency_cutoff, now)
                    else:
                        ts = _random_time(three_years_ago, now)
                    chunk.append(
                        Post(
                            author_id=author_id,
                            title=random.choice(title_pool),
                            body=random.choice(body_pool),
                            is_published=random.random() < 0.9,
                            view_count=random.randint(0, 5000),
                            created_at=ts,
                        )
                    )
                Post.objects.bulk_create(chunk, batch_size=BATCH)

        post_ids = list(Post.objects.values_list("id", flat=True))

        self.stdout.write("Attaching tags to posts...")
        through = Post.tags.through
        m2m_rows = []
        for pid in post_ids:
            # Renamed from `n_tags` to avoid shadowing the scaled tag-count
            # variable of the same name defined above.
            post_tag_count = max(1, int(random.gauss(TAGS_PER_POST_AVG, 1)))
            chosen = set()
            for _ in range(post_tag_count):
                # At low --scale, n_tags can equal len(HOT_SLUGS), leaving
                # cold_tag_ids empty; fall back to hot tags when that happens.
                if cold_tag_ids and random.random() >= 0.4:
                    chosen.add(random.choice(cold_tag_ids))
                else:
                    chosen.add(random.choice(hot_tag_ids))
            for tid in chosen:
                m2m_rows.append(through(post_id=pid, tag_id=tid))
            if len(m2m_rows) >= BATCH * 10:
                with transaction.atomic():
                    through.objects.bulk_create(m2m_rows, batch_size=BATCH, ignore_conflicts=True)
                m2m_rows = []
        if m2m_rows:
            with transaction.atomic():
                through.objects.bulk_create(m2m_rows, batch_size=BATCH, ignore_conflicts=True)

        self.stdout.write(f"Seeding {n_comments} comments...")
        post_weights = _long_tail_weights(len(post_ids), top_pct=0.01, top_share=0.5)
        # random.choices() rebuilds its cumulative-weight table on every call, so
        # drawing one at a time from a 100k-element weight list costs O(posts) per
        # comment. Hoist the table and draw a whole batch per call instead.
        post_cum = list(accumulate(post_weights))
        author_cum = author_cum_posts
        comment_pool = [
            fake.sentence(nb_words=random.randint(5, 30)) for _ in range(comment_pool_size)
        ]

        with transaction.atomic():
            for chunk_start in range(0, n_comments, BATCH):
                size = min(BATCH, n_comments - chunk_start)
                post_choices = random.choices(post_ids, cum_weights=post_cum, k=size)
                author_choices = random.choices(user_ids, cum_weights=author_cum, k=size)
                chunk = [
                    Comment(
                        post_id=pid,
                        author_id=aid,
                        body=random.choice(comment_pool),
                        created_at=_random_time(three_years_ago, now),
                    )
                    for pid, aid in zip(post_choices, author_choices, strict=True)
                ]
                Comment.objects.bulk_create(chunk, batch_size=BATCH)

        self.stdout.write(self.style.SUCCESS("Done."))


def _random_time(start, end):
    delta = end - start
    return start + timedelta(seconds=random.randint(0, int(delta.total_seconds())))


def _power_law_weights(n, top_n, top_share):
    weights = [1.0] * n
    bonus = (top_share * n) / max(top_n, 1)
    for i in range(min(top_n, n)):
        weights[i] = 1.0 + bonus
    return weights


def _long_tail_weights(n, top_pct, top_share):
    weights = [1.0] * n
    top_n = max(1, int(n * top_pct))
    bonus = (top_share * n) / top_n
    for i in range(top_n):
        weights[i] = 1.0 + bonus
    return weights
