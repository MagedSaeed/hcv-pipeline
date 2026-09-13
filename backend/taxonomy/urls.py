from rest_framework.routers import DefaultRouter

from taxonomy.views import HCVViewSet, TaxonomyViewSet

router = DefaultRouter()
router.register("taxonomies", TaxonomyViewSet, basename="taxonomy")
router.register("hcvs", HCVViewSet, basename="hcv")

urlpatterns = router.urls
