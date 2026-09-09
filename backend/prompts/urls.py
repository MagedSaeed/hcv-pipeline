from rest_framework.routers import DefaultRouter

from prompts.views import PromptTemplateViewSet

router = DefaultRouter()
router.register("prompts", PromptTemplateViewSet, basename="prompt")

urlpatterns = router.urls
