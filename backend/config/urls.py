from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

from llm.models_api import AvailableModelsView


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
    path("api/", include("core.urls")),
    path("api/", include("taxonomy.urls")),
    path("api/", include("corpus.urls")),
    path("api/", include("prompts.urls")),
    path("api/", include("pipeline.urls")),
    path("api/models/", AvailableModelsView.as_view(), name="available-models"),
]
