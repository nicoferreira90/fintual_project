import pytest
from django.test import Client

from blog.models import Post, Tag, User


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user(db):
    return User.objects.create(
        username="alice",
        email="alice@example.com",
        display_name="Alice",
    )


@pytest.mark.django_db
def test_list_posts_returns_published(client, user):
    tag = Tag.objects.create(name="Python", slug="python")
    post = Post.objects.create(author=user, title="Hello", body="World")
    post.tags.add(tag)
    Post.objects.create(author=user, title="Draft", body="...", is_published=False)

    response = client.get("/api/posts")

    assert response.status_code == 200
    data = response.json()
    titles = [p["title"] for p in data["items"]]
    assert "Hello" in titles
    assert "Draft" not in titles
    assert data["count"] == 1


@pytest.mark.django_db
def test_list_posts_does_not_n_plus_one(client, user, django_assert_num_queries):
    for i in range(5):
        post = Post.objects.create(author=user, title=f"P{i}", body="b")
        post.tags.add(Tag.objects.create(name=f"T{i}", slug=f"t{i}"))

    # count + page (author joined) + tags prefetch = 3, regardless of row count.
    with django_assert_num_queries(3):
        client.get("/api/posts")


@pytest.mark.django_db
def test_list_posts_limit_is_capped(client, user):
    Post.objects.create(author=user, title="Hello", body="World")

    response = client.get("/api/posts?limit=99999")

    # django-ninja's LimitOffsetPagination.Input enforces `le=NINJA_PAGINATION_MAX_LIMIT`
    # on the `limit` field itself, so an over-limit request is rejected by schema
    # validation (422) before the queryset ever runs, rather than silently truncated
    # to the cap. Either behavior satisfies "can't ask for the whole table back";
    # this is what the shipped pagination class actually does.
    assert response.status_code == 422


@pytest.mark.django_db
def test_get_post_returns_detail(client, user):
    post = Post.objects.create(author=user, title="Hello", body="World")

    response = client.get(f"/api/posts/{post.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Hello"
    assert data["author"]["username"] == "alice"
    assert data["comments"] == []
