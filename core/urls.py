from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import path
from ninja import NinjaAPI

from blog.api import router as blog_router

api = NinjaAPI()
api.add_router("/", blog_router)


def healthz(request):
    """Liveness. Deliberately does not touch the database: a liveness probe that
    fails on a database blip restarts every instance during a hiccup and turns a
    degradation into an outage."""
    return JsonResponse({"status": "ok"})


def readyz(request):
    """Readiness. Gates traffic, so it does check the database."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz),
    path("readyz", readyz),
    path("admin/", admin.site.urls),
    path("api/", api.urls),
]
