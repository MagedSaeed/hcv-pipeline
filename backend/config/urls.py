from django.conf import settings
from django.contrib import admin
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import include, path, re_path
from django.views.decorators.http import require_safe

from llm.models_api import AvailableModelsView


def health(_request):
    return JsonResponse({"status": "ok"})


@require_safe
def spa(_request):
    """The SPA shell for client-side routes such as /experiments/3."""
    index = settings.FRONTEND_DIST_DIR / "index.html"
    if not index.is_file():
        raise Http404("The frontend has not been built.")
    return HttpResponse(index.read_bytes(), content_type="text/html; charset=utf-8")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/", include("core.urls")),
    path("api/", include("taxonomy.urls")),
    path("api/", include("corpus.urls")),
    path("api/", include("prompts.urls")),
    path("api/", include("pipeline.urls")),
    path("api/models/", AvailableModelsView.as_view(), name="available-models"),
    # Last, and never for api/, admin/ or static/: an unknown API path stays a
    # 404, and /admin still gets APPEND_SLASH's redirect to /admin/.
    re_path(r"^(?!(?:api|admin|static)(?:/|$)).*$", spa, name="spa"),
]
