from django.db.models.deletion import ProtectedError
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from corpus.models import FewShotExample, Origin, ResourceFile, Scenario, SystemDescription
from corpus.permissions import ReadOnlyIfGenerated
from corpus.serializers import (
    FewShotExampleSerializer,
    ResourceFileSerializer,
    ScenarioSerializer,
    SystemDescriptionSerializer,
)


def integer_param(params, name):
    """Read a query parameter that filters on an integer column.

    An uncoerced value reaches the ORM and raises ValueError, which DRF does not
    map to a response: a typo in a query string would be a 500 rather than a 400.
    """
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except ValueError as error:
        raise ValidationError({name: f"Must be an integer, got {raw!r}."}) from error


class ResourceFileViewSet(viewsets.ModelViewSet):
    queryset = ResourceFile.objects.all()
    serializer_class = ResourceFileSerializer

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)

    def perform_destroy(self, instance):
        """A resource used by a few-shot example is protected at the database
        level. DRF has no handler for ProtectedError, so without this the first
        delete an admin tries on a seeded resource is a 500."""
        try:
            instance.delete()
        except ProtectedError as error:
            referenced_by = ", ".join(sorted(str(obj) for obj in error.protected_objects))
            raise ValidationError(
                f"'{instance.name}' is still referenced by: {referenced_by}. "
                "Remove or repoint those first."
            ) from error


class FewShotExampleViewSet(viewsets.ModelViewSet):
    serializer_class = FewShotExampleSerializer

    def get_queryset(self):
        queryset = FewShotExample.objects.all()
        phase = integer_param(self.request.query_params, "phase")
        return queryset.filter(phase=phase) if phase is not None else queryset


class SystemDescriptionViewSet(viewsets.ModelViewSet):
    serializer_class = SystemDescriptionSerializer
    permission_classes = [IsAuthenticated, ReadOnlyIfGenerated]

    def get_queryset(self):
        queryset = SystemDescription.objects.all()
        origin = self.request.query_params.get("origin")
        return queryset.filter(origin=origin) if origin else queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, origin=Origin.MANUAL)

    def perform_destroy(self, instance):
        """Deleting a target system cascades to the scenarios generated against
        it. ReadOnlyIfGenerated only guards the addressed row, so it would let a
        deletable seed row take a run's output down with it. Deletion of
        generated material is per run, never per row."""
        if instance.scenarios.filter(origin=Origin.GENERATED).exists():
            raise PermissionDenied(
                "This system description has generated scenarios attached to it, "
                "which deleting it would destroy. Delete the run that produced "
                "them instead."
            )
        instance.delete()


class ScenarioViewSet(viewsets.ModelViewSet):
    serializer_class = ScenarioSerializer
    permission_classes = [IsAuthenticated, ReadOnlyIfGenerated]

    def get_queryset(self):
        queryset = Scenario.objects.all()
        params = self.request.query_params
        if origin := params.get("origin"):
            queryset = queryset.filter(origin=origin)
        system = integer_param(params, "system")
        if system is not None:
            queryset = queryset.filter(system_description_id=system)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, origin=Origin.MANUAL)
