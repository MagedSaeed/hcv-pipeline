from rest_framework.routers import DefaultRouter

from corpus.views import (
    FewShotExampleViewSet,
    ResourceFileViewSet,
    ScenarioViewSet,
    SystemDescriptionViewSet,
)

router = DefaultRouter()
router.register("resources", ResourceFileViewSet, basename="resource")
router.register("fewshots", FewShotExampleViewSet, basename="fewshot")
router.register("system-descriptions", SystemDescriptionViewSet, basename="system-description")
router.register("scenarios", ScenarioViewSet, basename="scenario")

urlpatterns = router.urls
