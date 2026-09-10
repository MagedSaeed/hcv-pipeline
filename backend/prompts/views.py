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

    # The three fields PromptTemplate.COPIED_FIELDS calls "the text" (everything
    # except phase/name, which a draft preview gets from elsewhere — see
    # `_resolve_template`). Presence of these in a preview request is what
    # signals "render a draft", not the stored template's own row.
    DRAFT_FIELDS = ("system_prompt", "user_prompt", "output_schema")

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None, format=None):
        """Render this template exactly as a run would, and return both strings.

        If the body carries `user_prompt` (optionally alongside `system_prompt`
        and/or `output_schema`), that DRAFT body is rendered instead of the
        stored one — this row is then read only for `phase`/`name`, never for
        its own text, and nothing is ever written: no new version, no
        mutation, not even on a refused draft. This is what lets the editor
        validate an in-progress edit before saving it (spec §10) rather than
        having to save first — creating a version — to find out it was broken.

        `format=None`: see `activate` above for why every `@action` needs it.
        """
        template = self.get_object()
        return self._preview(request, template)

    @action(detail=False, methods=["post"], url_path="preview")
    def preview_unsaved(self, request, format=None):
        """Preview a template that has no id yet.

        The other half of "validate before saving": a template being authored
        for the first time has no persisted row to hang a detail route off,
        so `preview` above cannot reach it via `get_object()`. The body must
        supply `phase` and `user_prompt` (`system_prompt`/`output_schema` are
        optional, defaulting the same way a freshly-built `PromptTemplate`
        would); everything past that point — context building, `render()`,
        every isolation guard — is identical to `preview`, via `_preview`
        below, and nothing is ever persisted here either.

        `format=None`: see `activate` above for why every `@action` needs it.
        """
        return self._preview(request, None)

    def _preview(self, request, template):
        """The one preview implementation. `preview` and `preview_unsaved` are
        two ways of arriving here — a persisted row, or none at all — but from
        this point on there is exactly one code path: one `PreviewSerializer`,
        one `_context()`, one `render()`. A second, subtly different path for
        drafts is exactly how this project has produced taxonomy leaks before
        (see `prompts.rendering`'s module docstring); this keeps it to one.
        """
        options = PreviewSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        config = options.validated_data
        template = self._resolve_template(template, config)

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

    def _resolve_template(self, template, config):
        """The PromptTemplate render() actually sees: the persisted `template`
        row, or an UNSAVED stand-in built from a draft body. Neither branch
        ever touches the database — `PromptTemplate(...)` below constructs an
        ordinary Python object, never `.save()`d or `.create()`d — so a
        refused draft (a `StrictUndefined` error, a `TaxonomyLeak`) leaves no
        row behind and bumps no version. That is what makes "nothing
        persisted" true by construction rather than by a cleanup step that
        could be forgotten on some error path.

        A draft is signalled by `user_prompt` being present, not merely any
        of the three DRAFT_FIELDS: it is the one field a real PromptTemplate
        actually requires (`system_prompt`/`output_schema` both have model
        defaults), so a request that supplies one of those two WITHOUT
        `user_prompt` is refused rather than silently rendering a hybrid of
        "half draft, half whatever the stored row happens to have" that
        nobody asked for and the editor never sends.
        """
        overrides = {field: config[field] for field in self.DRAFT_FIELDS if field in config}
        if overrides and "user_prompt" not in overrides:
            raise ValidationError(
                {
                    "user_prompt": "A draft preview needs user_prompt alongside "
                    "system_prompt/output_schema, not just one of them."
                }
            )
        if template is None and "user_prompt" not in overrides:
            raise ValidationError(
                {"user_prompt": "Previewing a template with no id requires user_prompt."}
            )
        if not overrides:
            # No draft body at all: unchanged behaviour, the persisted row's
            # own stored text is what gets rendered.
            return template

        if template is None:
            if "phase" not in config:
                raise ValidationError(
                    {"phase": "Previewing a template with no id requires phase."}
                )
            phase, name = config["phase"], ""
        else:
            phase, name = template.phase, template.name

        return PromptTemplate(
            phase=phase,
            name=name,
            version=0,
            system_prompt=overrides.get("system_prompt", ""),
            user_prompt=overrides["user_prompt"],
            output_schema=overrides.get("output_schema", {}),
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
