from django.db.models.deletion import ProtectedError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.params import integer_param
from corpus.models import FewShotExample, Origin, ResourceFile, Scenario, SystemDescription
from corpus.permissions import ReadOnlyIfGenerated
from corpus.serializers import (
    FewShotExampleSerializer,
    ResourceFileSerializer,
    ScenarioBulkCreateSerializer,
    ScenarioSerializer,
    SystemDescriptionSerializer,
)
from pipeline.models import PhaseRun, labelled_elsewhere_error


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
        # `select_related`: the serializer's `is_scratch` reads
        # `generation_run.pipeline_run_id` for every generated row — one
        # query PER ROW on a paginated list without the join.
        queryset = SystemDescription.objects.select_related("generation_run")
        params = self.request.query_params
        if origin := params.get("origin"):
            queryset = queryset.filter(origin=origin)
        if variant := params.get("variant"):
            queryset = queryset.filter(generation_run__variant=variant)
        run = integer_param(params, "run")
        # `is not None`, not truthiness: `run=0` is a real (if nonexistent)
        # id and must filter to nothing, not fall through to "no filter was
        # given" the way `if run` would for the falsy integer 0.
        if run is not None:
            queryset = queryset.filter(generation_run__pipeline_run_id=run)
        phase_run = integer_param(params, "phase_run")
        if phase_run is not None:
            queryset = queryset.filter(generation_run_id=phase_run)
        return queryset

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
        elsewhere = labelled_elsewhere_error(instance.scenarios.all())
        if elsewhere:
            raise PermissionDenied(elsewhere)
        instance.delete()


class ScenarioViewSet(viewsets.ModelViewSet):
    serializer_class = ScenarioSerializer
    permission_classes = [IsAuthenticated, ReadOnlyIfGenerated]

    def get_queryset(self):
        # `intended_hcvs` is a ManyToManyField; `ScenarioSerializer` exposes
        # it as a `PrimaryKeyRelatedField(many=True)`, which reads
        # `instance.intended_hcvs.all()` PER ROW during serialization
        # (`ManyRelatedField.get_attribute`, which has no pk-only
        # optimization the way a plain FK does) — a page of 50 scenarios
        # costs 50 extra queries without this. `prefetch_related` pays for
        # the whole page in one extra query instead. `select_related` does
        # the same for `is_scratch`, which reads
        # `generation_run.pipeline_run_id` for every generated row.
        queryset = Scenario.objects.select_related("generation_run").prefetch_related(
            "intended_hcvs"
        )
        params = self.request.query_params
        if origin := params.get("origin"):
            queryset = queryset.filter(origin=origin)
        system = integer_param(params, "system")
        if system is not None:
            queryset = queryset.filter(system_description_id=system)
        if variant := params.get("variant"):
            queryset = queryset.filter(generation_run__variant=variant)
        run = integer_param(params, "run")
        # `is not None`, not truthiness — see SystemDescriptionViewSet's
        # identical comment; `run=0` must filter to nothing.
        if run is not None:
            queryset = queryset.filter(generation_run__pipeline_run_id=run)
        phase_run = integer_param(params, "phase_run")
        if phase_run is not None:
            queryset = queryset.filter(generation_run_id=phase_run)
        labelled_in_run = integer_param(params, "labelled_in_run")
        if labelled_in_run is not None:
            # Scenarios a phase-3-only repetition was launched to label.
            chosen = set()
            for ids in PhaseRun.objects.filter(
                pipeline_run_id=labelled_in_run, phase=3, scenario_ids__isnull=False
            ).values_list("scenario_ids", flat=True):
                chosen.update(ids)
            queryset = queryset.filter(id__in=chosen)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, origin=Origin.MANUAL)

    def perform_destroy(self, instance):
        """Refused while an experiment's labels point at this scenario."""
        elsewhere = labelled_elsewhere_error(Scenario.objects.filter(pk=instance.pk))
        if elsewhere:
            raise PermissionDenied(elsewhere)
        instance.delete()

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request, format=None):
        """Save drafted systems and scenarios in one transaction."""
        serializer = ScenarioBulkCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        created = serializer.save()
        return Response(
            {"scenario_ids": [scenario.pk for scenario in created]},
            status=status.HTTP_201_CREATED,
        )
