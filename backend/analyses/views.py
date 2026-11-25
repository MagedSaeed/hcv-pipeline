import re

from django.db import transaction
from django.db.models import Max, Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from analyses import extraction, pdf, preset, report, runs, selection, state
from analyses.models import Analysis, AnalysisCall, AnalysisItem, AnalysisRun, AnalysisScenario
from analyses.serializers import (
    AnalysisWriteSerializer,
    ItemCreateSerializer,
    ItemWriteSerializer,
    LabelRequestSerializer,
    ScenarioWriteSerializer,
    detail_json,
    item_json,
    run_json,
    scenario_json,
    summary_json,
)
from core.params import integer_param
from core.permissions import StaffWrites as BaseStaffWrites
from taxonomy.models import Taxonomy

_COPY_SUFFIX = re.compile(r" \(\d+\)$")


class StaffWrites(BaseStaffWrites):
    """Staff and analysts write; other researchers read."""

    message = "Only staff may create, change or run analyses."

    def has_permission(self, request, view):
        return super().has_permission(request, view) or request.user.is_analyst


class Refused(APIException):
    status_code = status.HTTP_409_CONFLICT


class Invalid(Refused):
    status_code = status.HTTP_400_BAD_REQUEST


class Unavailable(Refused):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


_REFUSALS = {"invalid": Invalid, "missing_key": Unavailable, "queue_unavailable": Unavailable}


def _refusal(error):
    return _REFUSALS.get(error.code, Refused)(error.detail, code=error.code)


def _require_idle(analysis):
    if state.active_run(analysis):
        raise Refused("Wait for the run to finish, or cancel it.", code="already_running")


def _require_room(analysis):
    if analysis.scenarios.filter(set_aside=False).count() >= runs.MAX_INCLUDED:
        raise Refused(
            f"An analysis can include {runs.MAX_INCLUDED} scenarios. Set one aside first.",
            code="over_limit",
        )


def _default_taxonomy():
    if taxonomy := Taxonomy.for_analyses():
        return taxonomy
    raise Refused(
        "No taxonomy is the default for analyses yet. Choose one on the Taxonomy page.",
        code="no_taxonomy",
    )


def _create_analysis(owner, name="", description=""):
    return Analysis.objects.create(
        owner=owner,
        name=name,
        description=description,
        taxonomy=_default_taxonomy(),
        extraction_settings=state.default_settings(state.EXTRACTION),
        generation_settings=state.default_settings(state.GENERATION),
        labelling_settings=state.default_settings(state.LABELLING),
        preset_version=preset.load().version,
    )


def _next_name(owner, name):
    base = _COPY_SUFFIX.sub("", name.strip())[:280] or "Untitled system"
    taken = set(Analysis.objects.filter(owner=owner).values_list("name", flat=True))
    number = 2
    while f"{base} ({number})" in taken:
        number += 1
    return f"{base} ({number})"


def _extraction_settings(value):
    model = value.get("model") if isinstance(value, dict) else None
    temperature = value.get("temperature") if isinstance(value, dict) else None
    if (
        set(value or {}) != {"model", "temperature"}
        or not isinstance(model, str)
        or not model.strip()
    ):
        raise ValidationError({"extraction_settings": "Choose a model and a temperature."})
    if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
        raise ValidationError({"extraction_settings": "Temperature must be from 0 to 2."})
    return {"model": model.strip(), "temperature": float(temperature)}


def _require_editable_extraction(analysis):
    if state.is_locked(analysis):
        raise Refused("The extracted values are fixed once generation starts.", code="locked")
    _require_idle(analysis)


_ELEMENT_FACTS = [name for name, spec in extraction.FIELDS.items() if spec.value_kind == "element"]


def _element_ids(analysis, exclude=None):
    return set(
        analysis.items.filter(kind="element").exclude(pk=exclude).values_list("ref", flat=True)
    )


def _rejected(analysis):
    return set(
        analysis.items.filter(kind="element", decision="rejected").values_list("ref", flat=True)
    )


def _check_ref(kind, ref, elements):
    """An element needs a new, well-formed id; a fact or control names an element."""
    if kind == AnalysisItem.Kind.ELEMENT:
        problem = extraction.check_id(ref) or (
            "Another element already has this id." if ref in elements else ""
        )
    else:
        problem = "" if ref in elements else f'"{ref}" names no element.'
    if problem:
        raise ValidationError({"ref": problem})


def _recheck(analysis):
    """Work out every value's problem again after a change."""
    items = list(analysis.items.all())
    fields = ("kind", "field", "ref", "value", "state")
    problems = extraction.recheck([{name: getattr(i, name) for name in fields} for i in items])
    for item, problem in zip(items, problems, strict=True):
        if item.problem != problem:
            item.problem = problem
            item.save(update_fields=["problem"])


class AnalysisViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated, StaffWrites]
    lookup_value_regex = "[0-9]+"

    def _visible(self):
        """Every analysis, or only an analyst's own."""
        user = self.request.user
        return Analysis.objects.filter(owner=user) if user.is_analyst else Analysis.objects.all()

    def get_queryset(self):
        return (
            self._visible()
            .select_related("owner", "taxonomy")
            .prefetch_related(
                # Polled every two seconds, so the prompts and replies stay in the database.
                Prefetch("runs", AnalysisRun.objects.defer("pinned", "targets")),
            )
        )

    def _loaded(self, pk):
        queryset = self.get_queryset().prefetch_related(
            "items",
            "scenarios__labels__hcv",
            Prefetch("scenarios__calls", AnalysisCall.objects.only("id", "scenario_id", "error")),
        )
        return get_object_or_404(queryset, pk=pk)

    def _held(self, pk):
        """The row, held until the transaction ends, so a start can't interleave."""
        return get_object_or_404(self._visible().select_for_update(), pk=pk)

    def list(self, request):
        runs.expire_stuck()
        queryset = self.get_queryset().prefetch_related("scenarios")
        if request.query_params.get("scope") != "all":
            queryset = queryset.filter(owner=request.user)
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response([summary_json(row) for row in page])

    def retrieve(self, request, pk=None):
        runs.expire_stuck(get_object_or_404(self._visible().only("id"), pk=pk))
        return Response(detail_json(self._loaded(pk)))

    def create(self, request):
        serializer = AnalysisWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data
        analysis = _create_analysis(
            request.user, fields.get("name", ""), fields.get("description", "")
        )
        return Response(detail_json(self._loaded(analysis.pk)), status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        serializer = AnalysisWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data
        with transaction.atomic():
            analysis = self._held(pk)
            runs.expire_stuck(analysis)
            self._apply(analysis, fields)
        return Response(detail_json(self._loaded(pk)))

    def _apply(self, analysis, fields):
        locked = state.is_locked(analysis)
        if "description" in fields and fields["description"] != analysis.description:
            if locked:
                raise Refused(
                    "The description is locked because scenarios were generated from it.",
                    code="locked",
                )
            analysis.description = fields["description"]
        new_taxonomy = fields.get("taxonomy")
        changed_taxonomy = new_taxonomy is not None and new_taxonomy.pk != analysis.taxonomy_id
        if changed_taxonomy:
            if locked:
                raise Refused("The taxonomy is fixed once generation starts.", code="locked")
            analysis.taxonomy = new_taxonomy
        if "name" in fields:
            if locked and not fields["name"].strip():
                raise ValidationError({"name": "Give the analysis a name."})
            analysis.name = fields["name"]
        if "extraction_settings" in fields:
            _require_idle(analysis)
            analysis.extraction_settings = _extraction_settings(fields["extraction_settings"])
        for kind in (state.GENERATION, state.LABELLING):
            field = f"{kind}_settings"
            if field not in fields and not changed_taxonomy:
                continue
            _require_idle(analysis)
            wanted = fields.get(field, getattr(analysis, field))
            try:
                cleaned = selection.validate(kind, wanted, analysis.taxonomy)
            except selection.SelectionError as error:
                raise ValidationError({field: error.message}) from error
            setattr(analysis, field, cleaned)
        analysis.save()

    def destroy(self, request, pk=None):
        with transaction.atomic():
            analysis = get_object_or_404(self._visible(), pk=pk)
            # Waits for a save in progress and stops any later one.
            analysis.runs.filter(status__in=AnalysisRun.ACTIVE).update(
                status=AnalysisRun.Status.CANCELLED
            )
            analysis.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def options(self, request, format=None):
        pk = integer_param(request.query_params, "taxonomy")
        taxonomy = _default_taxonomy() if pk is None else get_object_or_404(Taxonomy, pk=pk)
        extractor = preset.load_extractor()
        return Response(
            {
                **selection.options(taxonomy),
                "extraction": {
                    "fields": extraction.field_options(),
                    "controls": [*extraction.CONTROLS, *extraction.CONTROL_FAMILIES],
                    "defaults": state.default_settings(state.EXTRACTION),
                    "prompt": f"{extractor.name} v{extractor.version}",
                },
            }
        )

    @action(detail=True, methods=["post"], url_path="start-new")
    def start_new(self, request, pk=None, format=None):
        source = get_object_or_404(self._visible(), pk=pk)
        copy = _create_analysis(
            request.user, _next_name(request.user, source.name), source.description
        )
        return Response(detail_json(self._loaded(copy.pk)), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def extract(self, request, pk=None, format=None):
        analysis = get_object_or_404(self._visible(), pk=pk)
        try:
            run = runs.start_extraction(analysis, request.user)
        except runs.RunRefused as error:
            raise _refusal(error) from error
        return Response(run_json(run), status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"], url_path="items")
    def add_item(self, request, pk=None, format=None):
        serializer = ItemCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            analysis = self._held(pk)
            runs.expire_stuck(analysis)
            _require_editable_extraction(analysis)
            target = self._new_item(analysis, serializer.validated_data)
            target.save()
            _recheck(analysis)
            target.refresh_from_db()
            analysis.save(update_fields=["updated_at"])
        return Response(item_json(target, _rejected(analysis)), status=status.HTTP_201_CREATED)

    @staticmethod
    def _new_item(analysis, data):
        field, ref = data["field"], data["ref"].strip()
        elements = _element_ids(analysis)
        if field == "control":
            if "state" not in data:
                raise ValidationError({"state": "Choose present or absent."})
            kind, section = AnalysisItem.Kind.CONTROL, extraction.CONTROL_SECTION
            value, problem = extraction.check_control(data["value"])
        elif field in extraction.FIELDS:
            spec = extraction.FIELDS[field]
            kind, section = spec.kind, spec.section
            value, problem = extraction.check_value(field, data["value"], elements)
        else:
            raise ValidationError({"field": "Unknown field."})
        if problem:
            raise ValidationError({"value": problem})
        _check_ref(kind, ref, elements)
        last = analysis.items.aggregate(last=Max("order"))["last"] or 0
        item_state = data.get("state", "") if kind == AnalysisItem.Kind.CONTROL else ""
        return AnalysisItem(
            analysis=analysis,
            kind=kind,
            section=section,
            field=field,
            ref=ref,
            value=value,
            original_value=value,
            state=item_state,
            original_state=item_state,
            decision=AnalysisItem.Decision.APPROVED,
            added=True,
            order=last + 1,
        )

    @action(detail=True, methods=["patch", "delete"], url_path=r"items/(?P<item_pk>[0-9]+)")
    def item(self, request, pk=None, item_pk=None, format=None):
        if request.method == "DELETE":
            return self._delete_item(pk, item_pk)
        serializer = ItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = serializer.validated_data
        with transaction.atomic():
            analysis = self._held(pk)
            runs.expire_stuck(analysis)
            _require_editable_extraction(analysis)
            target = get_object_or_404(analysis.items, pk=item_pk)
            if "ref" in fields:
                self._move(analysis, target, fields["ref"].strip())
            if "value" in fields:
                target.value = self._checked_value(analysis, target, fields["value"])
            if "state" in fields:
                if target.kind != "control":
                    raise ValidationError({"state": "Only controls have a state."})
                target.state = fields["state"]
            target.save()
            _recheck(analysis)
            target.refresh_from_db()
            if "decision" in fields:
                if fields["decision"] == "approved" and target.problem:
                    raise Refused("Edit or reject this value first.", code="has_problem")
                target.decision = fields["decision"]
                target.save(update_fields=["decision"])
            analysis.save(update_fields=["updated_at"])
        return Response(item_json(target, _rejected(analysis)))

    def _delete_item(self, pk, item_pk):
        with transaction.atomic():
            analysis = self._held(pk)
            runs.expire_stuck(analysis)
            _require_editable_extraction(analysis)
            target = get_object_or_404(analysis.items, pk=item_pk)
            if not target.added:
                raise Refused("Reject a value the model extracted instead.", code="extracted")
            target.delete()
            _recheck(analysis)
            analysis.save(update_fields=["updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _move(analysis, target, ref):
        """Point a fact or control at another element, or rename an element and what names it."""
        old = target.ref
        if ref == old:
            return
        others = _element_ids(analysis, exclude=target.pk)
        _check_ref(target.kind, ref, others)
        target.ref = ref
        target.save(update_fields=["ref"])
        if target.kind != AnalysisItem.Kind.ELEMENT or old in others:
            return
        analysis.items.exclude(kind=AnalysisItem.Kind.ELEMENT).filter(ref=old).update(ref=ref)
        facts = analysis.items.filter(kind=AnalysisItem.Kind.FACT, field__in=_ELEMENT_FACTS)
        facts.filter(original_value=old).update(original_value=ref)
        facts.filter(value=old).update(value=ref)

    @staticmethod
    def _checked_value(analysis, target, value):
        if target.kind == "control":
            checked, problem = extraction.check_control(value)
        elif target.field in extraction.FIELDS:
            elements = set(analysis.items.filter(kind="element").values_list("ref", flat=True))
            checked, problem = extraction.check_value(target.field, value, elements)
        else:
            raise ValidationError({"value": "This field is unknown, so it can only be rejected."})
        if problem:
            raise ValidationError({"value": problem})
        return checked

    @action(detail=True, methods=["post"], url_path="items/approve-asserted")
    def approve_asserted(self, request, pk=None, format=None):
        with transaction.atomic():
            analysis = self._held(pk)
            _require_editable_extraction(analysis)
            analysis.items.filter(decision="pending", fact_type="Asserted", problem="").update(
                decision="approved"
            )
            analysis.save(update_fields=["updated_at"])
        return Response(detail_json(self._loaded(pk)))

    @action(detail=True, methods=["post"], url_path="items/approve-section")
    def approve_section(self, request, pk=None, format=None):
        section = request.data.get("section")
        if section not in extraction.SECTIONS:
            raise ValidationError({"section": "Unknown section."})
        with transaction.atomic():
            analysis = self._held(pk)
            _require_editable_extraction(analysis)
            analysis.items.filter(decision="pending", section=section, problem="").update(
                decision="approved"
            )
            analysis.save(update_fields=["updated_at"])
        return Response(detail_json(self._loaded(pk)))

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None, format=None):
        analysis = get_object_or_404(self._visible(), pk=pk)
        try:
            run = runs.start_generation(analysis, request.user)
        except runs.RunRefused as error:
            raise _refusal(error) from error
        return Response(run_json(run), status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def label(self, request, pk=None, format=None):
        serializer = LabelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        analysis = get_object_or_404(self._visible(), pk=pk)
        try:
            run = runs.start_labelling(analysis, request.user, **serializer.validated_data)
        except runs.RunRefused as error:
            raise _refusal(error) from error
        return Response(run_json(run), status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None, format=None):
        run = state.active_run(get_object_or_404(self._visible(), pk=pk))
        if run is None:
            raise Refused("Nothing is running.", code="not_running")
        return Response(run_json(runs.cancel(run)))

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None, format=None):
        return Response(report.build(get_object_or_404(self.get_queryset(), pk=pk)))

    @action(detail=True, methods=["get"], url_path="report/pdf")
    def report_pdf(self, request, pk=None, format=None):
        analysis = get_object_or_404(self.get_queryset(), pk=pk)
        order = {
            pk: number
            for number, pk in enumerate(analysis.scenarios.values_list("pk", flat=True), 1)
        }
        response = HttpResponse(
            pdf.render(report.build(analysis), order), content_type="application/pdf"
        )
        response["Content-Disposition"] = pdf.content_disposition(analysis.name)
        return response

    @action(detail=True, methods=["post"])
    def scenarios(self, request, pk=None, format=None):
        serializer = ScenarioWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        if "narrative" not in fields:
            raise ValidationError({"narrative": "Say what happens."})
        fields.pop("set_aside", None)
        with transaction.atomic():
            analysis = self._held(pk)
            runs.expire_stuck(analysis)
            _require_idle(analysis)
            if not state.is_locked(analysis):
                raise Refused("Generate scenarios first.", code="not_generated")
            _require_room(analysis)
            taken = set(analysis.scenarios.values_list("scenario_id", flat=True))
            number = analysis.own_sequence + 1
            while f"sc_{number}" in taken:
                number += 1
            analysis.own_sequence = number
            analysis.save(update_fields=["own_sequence", "updated_at"])
            scenario = AnalysisScenario.objects.create(
                analysis=analysis,
                scenario_id=f"sc_{number}",
                is_manual=True,
                order=number,
                **fields,
            )
        return Response(scenario_json(analysis, scenario), status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"scenarios/(?P<scenario_pk>[0-9]+)",
    )
    def scenario(self, request, pk=None, scenario_pk=None, format=None):
        with transaction.atomic():
            analysis = self._held(pk)
            runs.expire_stuck(analysis)
            scenario = get_object_or_404(analysis.scenarios, pk=scenario_pk)
            _require_idle(analysis)
            analysis.save(update_fields=["updated_at"])
            if request.method == "DELETE":
                if not scenario.is_manual:
                    raise Refused("Generated scenarios can only be set aside.", code="read_only")
                scenario.delete()
                return Response(status=status.HTTP_204_NO_CONTENT)

            serializer = ScenarioWriteSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            fields = serializer.validated_data
            if not scenario.is_manual and set(fields) - {"set_aside"}:
                raise Refused("Generated scenarios are read-only.", code="read_only")
            if scenario.set_aside and fields.get("set_aside") is False:
                _require_room(analysis)
            for name, value in fields.items():
                setattr(scenario, name, value)
            scenario.save(update_fields=list(fields))
        return Response(scenario_json(analysis, scenario))
