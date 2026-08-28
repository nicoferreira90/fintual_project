import pytest
from django.core.management import call_command

from blog.models import Comment, Post, Tag, User


@pytest.mark.django_db
def test_seed_scale_produces_proportional_rows():
    call_command("seed", "--scale", "0.001")

    assert User.objects.count() == 1
    assert Tag.objects.count() == 5
    assert Post.objects.count() == 100
    assert Comment.objects.count() == 500


@pytest.mark.django_db
def test_seed_is_a_noop_when_data_exists():
    User.objects.create(username="existing", email="e@example.com", display_name="E")

    call_command("seed", "--scale", "0.001")

    assert User.objects.count() == 1
    assert Post.objects.count() == 0
