from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from blog.models import Comment, Post, Tag, User
from blog.schemas import (
    CommentCreateIn,
    CommentCreateOut,
    PostCreateIn,
    PostCreateOut,
    PostDetailOut,
    PostListOut,
    UserDetailOut,
)

router = Router()

MAX_EMBEDDED_COMMENTS = 50


def _post_list_qs():
    """Base queryset for every list endpoint: one join for authors, one extra
    query for tags, regardless of page size."""
    return Post.objects.select_related("author").prefetch_related("tags")


@router.get("/posts", response=list[PostListOut])
@paginate
def list_posts(request):
    return _post_list_qs().filter(is_published=True).order_by("-created_at")


@router.get("/posts/by-tag/{slug}", response=list[PostListOut])
@paginate
def posts_by_tag(request, slug: str):
    tag = get_object_or_404(Tag, slug=slug)
    return _post_list_qs().filter(tags=tag, is_published=True).order_by("-created_at")


@router.get("/posts/search", response=list[PostListOut])
@paginate
def search_posts(request, q: str):
    query = SearchQuery(q, config="english")
    return (
        _post_list_qs()
        .filter(search_vector=query, is_published=True)
        .annotate(rank=SearchRank(F("search_vector"), query))
        .order_by("-rank", "-created_at")
    )


@router.get("/posts/{post_id}", response=PostDetailOut)
def get_post(request, post_id: int):
    post = get_object_or_404(_post_list_qs(), id=post_id)

    # Single-column atomic bump. post.save() rewrote the whole row (body
    # included) on every read, lost concurrent increments, and silently moved
    # updated_at because of auto_now.
    # ponytail: still one write per read. Batch in memory or move the counter to
    # Redis and flush periodically if this becomes the bottleneck.
    Post.objects.filter(pk=post.pk).update(view_count=F("view_count") + 1)
    post.view_count += 1

    # ponytail: hot posts carry tens of thousands of comments; cap rather than
    # stream. A paginated /posts/{id}/comments endpoint is the upgrade.
    comments = list(
        post.comments.select_related("author").order_by("created_at")[
            :MAX_EMBEDDED_COMMENTS
        ]
    )

    return {
        "id": post.id,
        "title": post.title,
        "body": post.body,
        "author": post.author,
        "tags": post.tags.all(),
        "comments": comments,
        "view_count": post.view_count,
        "created_at": post.created_at,
        "updated_at": post.updated_at,
    }


@router.post("/posts", response=PostCreateOut)
def create_post(request, payload: PostCreateIn):
    author = get_object_or_404(User, id=payload.author_id)
    with transaction.atomic():
        post = Post.objects.create(
            author=author,
            title=payload.title,
            body=payload.body,
        )
        if payload.tag_slugs:
            tags = list(Tag.objects.filter(slug__in=payload.tag_slugs))
            missing = set(payload.tag_slugs) - {tag.slug for tag in tags}
            if missing:
                raise HttpError(404, f"unknown tag slugs: {', '.join(sorted(missing))}")
            post.tags.set(tags)
    return {"id": post.id, "title": post.title}


@router.post("/posts/{post_id}/comments", response=CommentCreateOut)
def create_comment(request, post_id: int, payload: CommentCreateIn):
    post = get_object_or_404(Post, id=post_id)
    author = get_object_or_404(User, id=payload.author_id)
    comment = Comment.objects.create(post=post, author=author, body=payload.body)
    return {"id": comment.id}


@router.get("/users/find", response=UserDetailOut)
def find_user_by_email(request, email: str):
    user = get_object_or_404(User, email=email)
    return _user_detail(user)


@router.get("/users/{user_id}", response=UserDetailOut)
def get_user(request, user_id: int):
    user = get_object_or_404(User, id=user_id)
    return _user_detail(user)


def _user_detail(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "bio": user.bio,
        "post_count": user.posts.count(),
        "comment_count": user.comments.count(),
    }
