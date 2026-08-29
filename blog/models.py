from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(models.Model):
    username = models.CharField(max_length=64, unique=True)
    email = models.CharField(max_length=255, db_index=True)
    display_name = models.CharField(max_length=128)
    bio = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.username


class Tag(models.Model):
    name = models.CharField(max_length=64, unique=True)
    slug = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return self.name


class Post(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="posts")
    title = models.CharField(max_length=255)
    body = models.TextField()
    is_published = models.BooleanField(default=True)
    view_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    tags = models.ManyToManyField(Tag, related_name="posts", blank=True)

    class Meta:
        indexes = [
            # Partial: the list endpoint only ever reads published rows, so the
            # index stays smaller than a plain composite on (is_published, created_at).
            models.Index(
                fields=["-created_at"],
                condition=Q(is_published=True),
                name="post_published_recent_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.title


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="comments")
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["post", "created_at"], name="comment_post_created_idx"),
        ]
