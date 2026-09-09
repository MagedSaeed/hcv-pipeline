from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from taxonomy.models import Taxonomy
from taxonomy.serializers import HCVSerializer, TaxonomySerializer


class TaxonomyViewSet(viewsets.ReadOnlyModelViewSet):
    """Taxonomies are read-only over the API; they arrive by import command."""

    queryset = Taxonomy.objects.all()
    serializer_class = TaxonomySerializer

    @action(detail=True, methods=["get"])
    def hcvs(self, request, pk=None, format=None):
        """Paginated like every other list endpoint, so the response shape of
        the API is uniform even though a taxonomy is only ever a few dozen rows.

        `format=None`: `DefaultRouter` generates a format-suffix route
        (`.../hcvs.json`) for every `@action`, which passes `format` as a
        URL kwarg — every action needs the parameter or that route 500s
        with `TypeError`.
        """
        rows = self.get_object().hcvs.all()
        page = self.paginate_queryset(rows)
        if page is not None:
            return self.get_paginated_response(HCVSerializer(page, many=True).data)
        return Response(HCVSerializer(rows, many=True).data)
