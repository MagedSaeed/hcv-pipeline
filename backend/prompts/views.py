from django.db import transaction
from django.db.models.deletion import ProtectedError
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
    VARIANT_UNBIASED,
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    build_phase3_context,
    render,
    unscannable_hcv_rows,
)
from prompts.serializers import PreviewSerializer, PromptTemplateSerializer
from taxonomy.models import Taxonomy


def _make_active(template):
    """A newly saved version becomes the active one: the latest edit is what new
    experiments use unless someone deliberately makes an older version active."""
    PromptTemplate.objects.filter(phase=template.phase, name=template.name).exclude(
        pk=template.pk
    ).update(is_active=False)
    template.is_active = True
    template.save(update_fields=["is_active"])


class StaffWrites(BaseStaffWrites):
    """Anyone signed in may read a prompt; only staff may author one."""

    message = "Only staff may create or edit prompt templates."


class PromptTemplateViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """List/create/retrieve/update/delete.

    A version a run or an experiment points at is a provenance record, and the
    PROTECT foreign keys refuse its deletion; `perform_destroy` turns that into a
    400 naming what still uses it. Unused versions can be deleted freely.
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
        with transaction.atomic():
            created = PromptTemplate.objects.create_version(
                phase=phase, name=name, created_by=self.request.user, **data
            )
            _make_active(created)
        serializer.instance = created

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError as error:
            users = sorted({str(obj) for obj in error.protected_objects})
            shown = ", ".join(users[:5]) + (f" and {len(users) - 5} more" if len(users) > 5 else "")
            raise ValidationError(
                {
                    "detail": f"{instance} is still used by: {shown}. "
                    "Point those at another version, or delete them first."
                }
            ) from error

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
        with transaction.atomic():
            created = template.new_version(created_by=request.user, **changes)
            _make_active(created)
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

        # Fail CLOSED, mirroring `assert_request_not_leaking_taxonomy` (the
        # final choke point a real run passes through immediately before the
        # HTTP call): a variant-A render for which the backstop could not
        # actually run — no `guard_taxonomy` supplied and no active Taxonomy,
        # or a guard taxonomy with no scannable rows — must never be
        # presentable as a clean 200. Coordinator review: an unbound
        # `guard_taxonomy` (or none at all) previously still returned 200
        # with `leak_scan_ran: false`, which read as "checked, and clean" to
        # anyone who didn't read that field closely — the same defect shape
        # `pipeline.models.unbound_guard_taxonomy_error` took four rounds to
        # close on the run path, now reachable again in a single stateless
        # preview request. `rendered.leak_scan_ran` already carries exactly
        # the fact needed: True only when `assert_no_taxonomy_leak` genuinely
        # compared the render against real HCV content. Scoped to variant-A
        # GENERATION contexts specifically (`context["_variant"]`, never set
        # for phase 3 labelling, which always legitimately sees the taxonomy
        # and has nothing to scan for) rather than to the flag alone.
        if context.get("_variant") == VARIANT_UNBIASED and not rendered.leak_scan_ran:
            raise ValidationError(
                {
                    "detail": _unscanned_preview_detail(
                        context.get("_guard_taxonomy"),
                        explicit=bool(config.get("guard_taxonomy")),
                    )
                }
            )

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

        A PARTIAL draft — say, only `system_prompt` edited — falls back to
        the PERSISTED row's own stored value for whichever of the three
        fields it does not supply, never to a bare default of `""`/`{}`.
        Coordinator review: a draft that edited only `user_prompt` used to
        drop the stored `output_schema` to `{}` in the process, so a template
        whose real, saved schema enumerates HCV ids previewed as a clean 200
        under variant A — the endpoint's own promise ("exactly as a run
        would") broken in exactly the direction that matters least safely.
        Falling back to the STORED value for every field a draft omits is
        what keeps that promise true for a partial edit, not just a full
        one — a draft always previews against "the stored row, with these
        specific edits applied", the same shape `new_version()`/`PATCH`
        already treat as an edit (`PromptTemplate.COPIED_FIELDS`).

        There is nothing to fall back to for a template with no id at all —
        `user_prompt` is REQUIRED there (the one field the model itself
        requires; `system_prompt`/`output_schema` still default to `""`/`{}`,
        the same defaults a freshly-created row would get).
        """
        overrides = {field: config[field] for field in self.DRAFT_FIELDS if field in config}
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
                raise ValidationError({"phase": "Previewing a template with no id requires phase."})
            phase, name = config["phase"], ""
            system_prompt = overrides.get("system_prompt", "")
            user_prompt = overrides["user_prompt"]
            output_schema = overrides.get("output_schema", {})
        else:
            phase, name = template.phase, template.name
            system_prompt = overrides.get("system_prompt", template.system_prompt)
            user_prompt = overrides.get("user_prompt", template.user_prompt)
            output_schema = overrides.get("output_schema", template.output_schema)

        return PromptTemplate(
            phase=phase,
            name=name,
            version=0,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=output_schema,
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


def _unscanned_preview_detail(guard, *, explicit):
    """The 400 for a variant-A preview whose backstop could not run, naming
    what is actually wrong.

    `assert_no_taxonomy_leak` returns False for three different reasons that
    need three different fixes: there is no guard taxonomy at all (pass one,
    or activate one); the guard has no HCV rows (a broken import); or some
    of its rows carry no content — decided by `unscannable_hcv_rows`, the
    SAME predicate the scan itself uses, so this cannot disagree with it.
    One message for all three used to tell an operator who HAD passed
    `guard_taxonomy=` to "Pass guard_taxonomy=". `explicit` says whether the
    guard came from the request or from the `Taxonomy.active()` fallback, so
    the message names the taxonomy the operator actually needs to look at.
    """
    if guard is None:
        return (
            "No taxonomy to check this variant A preview against. Activate one on the "
            "Taxonomy page, then preview again."
        )
    source = f"The taxonomy you chose ({guard})" if explicit else f"The active taxonomy ({guard})"
    rows = list(guard.hcvs.all())
    blank = unscannable_hcv_rows(rows)
    if not rows:
        problem = "has no HCV rows"
    elif blank:
        problem = f"has {len(blank)} of {len(rows)} rows with no id, name or definition"
    else:
        problem = "could not be scanned against"
    return (
        f"{source} {problem}, so this variant A preview couldn't be checked for "
        "taxonomy content. Fix that taxonomy on the Taxonomy page, or use a complete one."
    )
