from rest_framework.routers import DefaultRouter

from taxonomy.views import TaxonomyViewSet

router = DefaultRouter()
router.register("taxonomies", TaxonomyViewSet, basename="taxonomy")

urlpatterns = router.urls
