from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import StaffWrites as BaseStaffWrites
from corpus.models import FewShotExample, Scenario, SystemDescription
from prompts.models import PromptTemplate
from prompts.rendering import (
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    build_phase3_context,
    render,
)
from prompts.serializers import PreviewSerializer, PromptTemplateSerializer
from taxonomy.models import Taxonomy


class StaffWrites(BaseStaffWrites):
    """Anyone signed in may read a prompt; only staff may author one."""

    message = "Only staff may create or edit prompt templates."


class PromptTemplateViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """List/create/retrieve/update, deliberately no destroy.

    A template version is a provenance record: a run pins its id, so deleting one
    is unrecoverable — worse than the in-place mutation this task exists to
    prevent, since a mutated row could at least be reconstructed from history.
    `DestroyModelMixin` is left out entirely rather than included and blocked, so
    the router never binds a `delete` handler for the detail route and
    `DELETE /api/prompts/{id}/` is a plain 405 (the operation does not exist),
    not a 403 (the operation exists but you may not do it).
    """

    serializer_class = PromptTemplateSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def get_queryset(self):
        queryset = PromptTemplate.objects.all()
        phase = self.request.query_params.get("phase")
        if phase in (None, ""):
            return queryset
        try:
            return queryset.filter(phase=int(phase))
        except (TypeError, ValueError) as error:
            raise ValidationError({"phase": f"Must be an integer, got {phase!r}."}) from error

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        phase = data.pop("phase")
        name = data.pop("name")
        serializer.instance = PromptTemplate.objects.create_version(
            phase=phase, name=name, created_by=self.request.user, **data
        )

    def update(self, request, *args, **kwargs):
        """Editing a template creates the next version rather than mutating this one.

        A run pins a template id, so mutating a row in place would rewrite the
        wording behind results that already exist.
        """
        template = self.get_object()
        serializer = self.get_serializer(template, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = {
            field: value
            for field, value in serializer.validated_data.items()
            if field in PromptTemplate.COPIED_FIELDS
        }
        created = template.new_version(created_by=request.user, **changes)
        return Response(self.get_serializer(created).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None, format=None):
        """Make this version the active one for its (phase, name).

        Activation is a deliberate act, not a field edit: exactly one version of a
        named template is live, and flipping it through PATCH would have been
        silently dropped by the version-on-edit path (is_active is read-only on
        the serializer for exactly that reason).

        `format=None`: `DefaultRouter` generates a format-suffix route
        (`.../activate.json`) for every `@action`, which passes `format` as
        a URL kwarg — every action needs the parameter or that route 500s
        with `TypeError: ... got an unexpected keyword argument 'format'`.
        """
        template = self.get_object()
        # Both halves or neither: deactivating the siblings and activating this
        # row are one act. A failure between them would leave the (phase, name)
        # with NO active version at all — worse than the state it started in.
        with transaction.atomic():
            PromptTemplate.objects.filter(phase=template.phase, name=template.name).exclude(
                pk=template.pk
            ).update(is_active=False)
            template.is_active = True
            template.save(update_fields=["is_active"])
        return Response(self.get_serializer(template).data)

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None, format=None):
        """Render this template exactly as a run would, and return both strings.

        `format=None`: see `activate` above for why every `@action` needs it.
        """
        template = self.get_object()
        options = PreviewSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        config = options.validated_data

        taxonomy = (
            get_object_or_404(Taxonomy, pk=config["taxonomy"]) if config.get("taxonomy") else None
        )
        try:
            context = self._context(template, config, taxonomy)
            rendered = render(template, context)
        except (TaxonomyLeak, PromptRenderError) as error:
            raise ValidationError({"detail": str(error)}) from error

        return Response(
            {
                "system_prompt": rendered.system,
                "user_prompt": rendered.user,
                # The schema ships in the same request body as the two prompts,
                # so a preview that omitted it would be showing the admin less
                # than what is actually sent — including, for a Run A template,
                # a schema that may carry the taxonomy.
                "output_schema": template.output_schema,
                # Whether the Run A backstop actually ran, and against what. A
                # 200 with leak_scan_ran=false means nothing was checked; it is
                # not a clean bill of health.
                "guarded_by": rendered.guarded_by,
                "leak_scan_ran": rendered.leak_scan_ran,
            }
        )

    def _context(self, template, config, taxonomy):
        if template.phase == 3:
            if not config.get("scenario"):
                raise ValidationError(
                    {"scenario": "Previewing a phase 3 template requires a scenario id."}
                )
            scenario = get_object_or_404(Scenario, pk=config["scenario"])
            return build_phase3_context(
                scenario=scenario,
                system_description=scenario.system_description,
                taxonomy=taxonomy,
                fewshots=self._fewshots(config, phase=3),
            )
        target = (
            get_object_or_404(SystemDescription, pk=config["target_system"])
            if config.get("target_system")
            else None
        )
        guard = (
            get_object_or_404(Taxonomy, pk=config["guard_taxonomy"])
            if config.get("guard_taxonomy")
            else None
        )
        return build_phase2_context(
            variant=config.get("variant", "A"),
            seed_systems=SystemDescription.objects.filter(pk__in=config.get("seed_systems", [])),
            fewshots=self._fewshots(config, phase=2),
            generate_systems=config["generate_systems"],
            generate_scenarios=config["generate_scenarios"],
            n_systems=config["n_systems"],
            n_scenarios_per_system=config["n_scenarios_per_system"],
            target_system=target,
            taxonomy=taxonomy,
            guard_taxonomy=guard,
        )

    def _fewshots(self, config, *, phase):
        return FewShotExample.objects.filter(pk__in=config.get("fewshots", []), phase=phase)
