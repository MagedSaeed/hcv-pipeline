from rest_framework.routers import DefaultRouter

from analyses.views import AnalysisViewSet

router = DefaultRouter()
router.register("analyses", AnalysisViewSet, basename="analysis")

urlpatterns = router.urls
