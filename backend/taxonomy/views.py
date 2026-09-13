from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import StaffWrites as BaseStaffWrites
from taxonomy.models import HCV, Taxonomy
from taxonomy.serializers import HCVSerializer, TaxonomySerializer, taxonomy_lock_reason


class StaffWrites(BaseStaffWrites):
    message = "Only staff may edit a taxonomy."


def _refuse_if_locked(taxonomy):
    """A taxonomy a run has used is the record of what that run was shown and
    guarded against, so its rows are frozen; copy it to a new version instead."""
    reason = taxonomy_lock_reason(taxonomy)
    if reason:
        raise ValidationError({"detail": reason})


class TaxonomyViewSet(viewsets.ReadOnlyModelViewSet):
    """Taxonomies are imported by command; their HCV rows are editable until a run
    uses them, after which a new version is the way to change them."""

    queryset = Taxonomy.objects.all()
    serializer_class = TaxonomySerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    @action(detail=True, methods=["get", "post"])
    def hcvs(self, request, pk=None, format=None):
        """GET lists the rows (paginated like every list endpoint); POST adds one.

        `format=None`: `DefaultRouter` generates a format-suffix route
        (`.../hcvs.json`) for every `@action`, which passes `format` as a
        URL kwarg — every action needs the parameter or that route 500s
        with `TypeError`.
        """
        taxonomy = self.get_object()
        if request.method == "POST":
            _refuse_if_locked(taxonomy)
            serializer = HCVSerializer(data=request.data, context={"taxonomy": taxonomy})
            serializer.is_valid(raise_exception=True)
            serializer.save(taxonomy=taxonomy)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        rows = taxonomy.hcvs.all()
        page = self.paginate_queryset(rows)
        if page is not None:
            return self.get_paginated_response(HCVSerializer(page, many=True).data)
        return Response(HCVSerializer(rows, many=True).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None, format=None):
        taxonomy = self.get_object()
        with transaction.atomic():
            Taxonomy.objects.exclude(pk=taxonomy.pk).filter(is_active=True).update(is_active=False)
            taxonomy.is_active = True
            taxonomy.save(update_fields=["is_active"])
        return Response(self.get_serializer(taxonomy).data)

    @action(detail=True, methods=["post"], url_path="new-version")
    def new_version(self, request, pk=None, format=None):
        """Copy this taxonomy and all its HCV rows into a new, inactive version."""
        source = self.get_object()
        version = str(request.data.get("version", "")).strip()
        if not version:
            raise ValidationError({"version": "Give the new version a name, e.g. 1.2."})
        try:
            with transaction.atomic():
                copy = Taxonomy.objects.create(
                    vocabulary_id=source.vocabulary_id,
                    version=version,
                    notes=source.notes,
                    source_json=source.source_json,
                    is_active=False,
                )
                HCV.objects.bulk_create(
                    HCV(
                        taxonomy=copy,
                        hcv_id=row.hcv_id,
                        name=row.name,
                        definition=row.definition,
                        condition_classes=row.condition_classes,
                        order=row.order,
                    )
                    for row in source.hcvs.all()
                )
        except IntegrityError as error:
            raise ValidationError(
                {"version": f"{source.vocabulary_id} v{version} already exists."}
            ) from error
        return Response(self.get_serializer(copy).data, status=status.HTTP_201_CREATED)


class HCVViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = HCV.objects.select_related("taxonomy")
    serializer_class = HCVSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if "pk" in self.kwargs:
            context["taxonomy"] = get_object_or_404(HCV, pk=self.kwargs["pk"]).taxonomy
        return context

    def perform_update(self, serializer):
        _refuse_if_locked(serializer.instance.taxonomy)
        serializer.save()

    def perform_destroy(self, instance):
        _refuse_if_locked(instance.taxonomy)
        instance.delete()
