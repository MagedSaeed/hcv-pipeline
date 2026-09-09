from rest_framework.routers import DefaultRouter

from pipeline.views import ExperimentViewSet, LabelViewSet, PhaseRunViewSet, PipelineRunViewSet

router = DefaultRouter()
router.register("experiments", ExperimentViewSet, basename="experiment")
router.register("pipeline-runs", PipelineRunViewSet, basename="pipeline-run")
router.register("phase-runs", PhaseRunViewSet, basename="phase-run")
router.register("labels", LabelViewSet, basename="label")

urlpatterns = router.urls
