import json

import pytest
from django.test import Client

from blog.models import Post, User


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        username="dave", email="dave@example.com", display_name="Dave"
    )


@pytest.mark.django_db
def test_view_count_increments_without_touching_updated_at(client, user):
    post = Post.objects.create(author=user, title="T", body="B")
    before = Post.objects.get(pk=post.pk).updated_at

    response = client.get(f"/api/posts/{post.id}")

    assert response.json()["view_count"] == 1
    refreshed = Post.objects.get(pk=post.pk)
    assert refreshed.view_count == 1
    assert refreshed.updated_at == before


@pytest.mark.django_db
def test_create_post_with_unknown_tag_returns_404(client, user):
    response = client.post(
        "/api/posts",
        data=json.dumps(
            {"author_id": user.id, "title": "T", "body": "B", "tag_slugs": ["nope"]}
        ),
        content_type="application/json",
    )

    assert response.status_code == 404
    assert not Post.objects.filter(title="T").exists()
