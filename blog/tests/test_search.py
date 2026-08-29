import pytest
from django.test import Client

from blog.models import Post, User


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        username="carol", email="carol@example.com", display_name="Carol"
    )


@pytest.mark.django_db
def test_search_stems_and_matches_title(client, user):
    Post.objects.create(author=user, title="Running fast", body="body")

    response = client.get("/api/posts/search?q=runs")

    assert [p["title"] for p in response.json()["items"]] == ["Running fast"]
