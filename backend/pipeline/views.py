import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Sum
from django.http import HttpResponse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.keys import MissingAPIKey
from core.permissions import StaffWrites as BaseStaffWrites
from pipeline.export import build_bundle, safe_filename_stem, write_csv_zip
from pipeline.models import Experiment, Label, PhaseRun, PipelineRun
from pipeline.orchestration import QueueingError, launch, queue_runs, retry_failed
from pipeline.serializers import (
    ExperimentSerializer,
    LabelSerializer,
    LLMCallSerializer,
    PhaseRunSerializer,
    PipelineRunSerializer,
    RunRequestSerializer,
)


class StaffWrites(BaseStaffWrites):
    """Anyone signed in may read; only staff may create or launch."""

    message = "Only staff may create experiments or launch runs."


class Conflict(APIException):
    """The request is well-formed, but the resource's current state refuses it."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This action conflicts with the resource's current state."
    default_code = "conflict"


class ServiceUnavailable(APIException):
    """A 503 the operator can act on: something external isn't reachable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "service_unavailable"


class MissingAPIKeyError(ServiceUnavailable):
    """No OpenRouter key is configured. Distinct `code` from `BrokerUnavailableError`
    so a frontend can give different guidance ("set your key" vs. "the queue is
    down") without matching on the English wording of `detail`."""

    default_code = "missing_api_key"


class BrokerUnavailableError(ServiceUnavailable):
    """`queue_runs` could not hand a run to django-q. See `MissingAPIKeyError`
    for why this needs its own `code` rather than sharing one 503 reason."""

    default_code = "broker_unavailable"


class ExperimentViewSet(viewsets.ModelViewSet):
    serializer_class = ExperimentSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def get_queryset(self):
        """Annotate the roll-ups the serializer needs, once, for the whole page.

        Without the annotations, `ExperimentSerializer`'s `run_count` and
        `progress` fields each cost one query PER ROW — a COUNT for
        `run_count` and an aggregate SUM for `progress`. `Count("runs", ...)`
        and the three `Sum("runs__phase_runs__...")` annotations below all
        walk the SAME `runs` join, so the fan-out through `phase_runs` that
        the Sum needs does not inflate the distinct run Count, and vice versa
        (verified empirically in `test_experiment_api.py`, not just assumed).
        `ExperimentSerializer.get_run_count` and `ProgressField` both check
        for these annotations and fall back to the live property/count when
        they are absent — e.g. straight after `perform_create`, where the
        just-saved instance never went through this queryset at all.

        `prefetch_related` closes the other half of the same N+1: `seed_systems`
        and `fewshots` are plain M2M fields, and DRF's default
        `PrimaryKeyRelatedField` list rendering iterates each instance's
        manager separately — one more query per row per M2M field — unless
        prefetched here, once, for the whole page.

        The trailing `order_by` is not decorative: `Experiment.Meta.ordering`
        does not apply to a GROUP BY queryset (Django's `QuerySet.ordered`
        is False the moment an aggregate annotation is present, "a default
        ordering doesn't affect GROUP BY queries"), so without repeating it
        explicitly here the paginator would warn — correctly — that page
        results are not guaranteed stable.
        """
        return (
            Experiment.objects.annotate(
                run_count_annotated=Count("runs", distinct=True),
                progress_total=Sum("runs__phase_runs__total_children"),
                progress_completed=Sum("runs__phase_runs__completed_children"),
                progress_failed=Sum("runs__phase_runs__failed_children"),
            )
            .prefetch_related("seed_systems", "fewshots")
            .order_by("-created_at")
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def initial(self, request, *args, **kwargs):
        """Skip DRF's own content negotiation for `export`.

        DRF's `DefaultContentNegotiation` treats `?format=` as its
        `URL_FORMAT_OVERRIDE` too — used to pick a RESPONSE renderer — and
        raises `Http404` the instant that value doesn't name a configured
        renderer's `.format`. `export`'s `?format=csv` (a real, deliberately
        unregistered renderer) and `?format=xml` (its deliberately-invalid
        case) both did that during `initial()`, before `export`'s own body
        ever ran: a 404 instead of the 200/400 the action was meant to give.
        `export` always returns a plain `HttpResponse`, never a DRF
        `Response` — `finalize_response` only reads `request.accepted_renderer`
        for the latter — so skipping negotiation here changes nothing else
        about how it behaves. `determine_version` is still called, though:
        skipping it left `request.version`/`request.versioning_scheme` unset,
        harmless today since nothing reads them, but an `AttributeError`
        waiting for the day a versioning class is configured.
        """
        if self.action == "export":
            self.format_kwarg = self.get_format_suffix(**kwargs)
            version, scheme = self.determine_version(request, *args, **kwargs)
            request.version, request.versioning_scheme = version, scheme
            self.perform_authentication(request)
            self.check_permissions(request)
            self.check_throttles(request)
            return
        super().initial(request, *args, **kwargs)

    def perform_destroy(self, instance):
        """Refuse to delete an experiment with any runs recorded against it.

        The spec makes generated output immutable and the archive the record
        of what a model actually produced — cascading a DELETE through an
        experiment's runs, phase runs, calls and labels would erase exactly
        that record, and would do so with a 204 even while a worker still
        holds a RUNNING PipelineRun. An experiment nobody has ever launched
        carries no such record and stays deletable.
        """
        if instance.runs.exists():
            raise Conflict(
                f"{instance.name!r} has runs recorded against it and cannot be "
                "deleted. Delete each of its runs via DELETE /api/pipeline-runs/"
                "{id}/ first if you really want to remove it."
            )
        instance.delete()

    @action(detail=True, methods=["post"])
    def run(self, request, pk=None, format=None):
        """Launch N repetitions and hand each to django-q.

        `format=None`: `DefaultRouter` generates a format-suffix route
        (`.../run.json`) for every `@action`, which passes `format` as a URL
        kwarg — every action needs the parameter or that route 500s with
        `TypeError`. See `export` below for the fuller story.

        `launch` resolves the API key first, so a missing key creates nothing —
        the run fails here with an actionable message rather than producing a
        batch full of 401s. Queueing itself goes through `queue_runs`, shared
        with `run_experiment`'s non-inline path, so a broker error reverts
        the one run that failed rather than stranding it QUEUED forever — an
        unreachable broker is the same class of operator problem as a missing
        API key, so it is reported the same way: 503, not a 500. Both carry a
        distinct, machine-readable `code` (`MissingAPIKeyError`/
        `BrokerUnavailableError`'s own `default_code`) alongside the
        human-readable `detail`, so a frontend can act on the reason rather
        than pattern-matching the English message.
        """
        experiment = self.get_object()
        options = RunRequestSerializer(data=request.data)
        options.is_valid(raise_exception=True)

        try:
            runs = launch(experiment, n_repetitions=options.validated_data["n_repetitions"])
        except MissingAPIKey as error:
            return self._service_unavailable(MissingAPIKeyError, error)
        except DjangoValidationError as error:
            raise ValidationError(getattr(error, "message_dict", {"detail": str(error)})) from error

        try:
            queue_runs(runs)
        except QueueingError as error:
            return self._service_unavailable(BrokerUnavailableError, error)

        return Response(
            {"runs": [{"id": run.pk, "repetition_index": run.repetition_index} for run in runs]},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None, format=None):
        # `format=None`: see `export` below for why every action needs it.
        return Response(self.get_object().progress)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None, format=None):
        """Download the archival bundle. Any authenticated user may.

        `format=None` in the signature is required, not decorative:
        `DefaultRouter` also generates a format-SUFFIX route for every
        action (e.g. `GET .../export.csv`), which passes `format` as a URL
        kwarg — omitting the parameter raised `TypeError: export() got an
        unexpected keyword argument 'format'` (a 500) the moment that route
        matched. It is used here only as a fallback when the query param is
        absent, so `?format=` — what the tests and the frontend actually
        use — still wins.
        """
        experiment = self.get_object()
        # `.lower()`: `?format=CSV` used to 400 for no reason a caller could
        # anticipate from the 400's own message.
        wanted = (request.query_params.get("format") or format or "json").lower()
        if wanted not in ("json", "csv"):
            raise ValidationError({"format": "Use format=json or format=csv."})

        bundle = build_bundle(experiment)
        stem = f"{safe_filename_stem(experiment.name)}-export"
        if wanted == "csv":
            response = HttpResponse(write_csv_zip(bundle), content_type="application/zip")
            response["Content-Disposition"] = f'attachment; filename="{stem}.zip"'
            return response

        response = HttpResponse(
            json.dumps(bundle, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        response["Content-Disposition"] = f'attachment; filename="{stem}.json"'
        return response

    @staticmethod
    def _service_unavailable(error_class, error):
        """Build a 503 body carrying both the live message and a stable `code`.

        `run` catches plain, non-DRF exceptions (`MissingAPIKey`,
        `QueueingError`) rather than letting DRF's own exception handler
        render them, since each needs the ORIGINAL exception's dynamic
        message as `detail` — not `error_class`'s generic `default_detail`.
        Constructing the DRF exception anyway (`error_class(str(error))`)
        and reading its `.detail`/`.detail.code` back out means the `code`
        for each case still comes from exactly one place — the exception
        class's `default_code` — rather than a bare string literal repeated
        wherever a 503 of that kind gets built.
        """
        exc = error_class(str(error))
        return Response(
            {"detail": str(exc.detail), "code": exc.detail.code}, status=exc.status_code
        )


def integer_param(params, name):
    """Read an integer query parameter, or 400. Never a 500."""
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as error:
        raise ValidationError({name: f"Must be an integer, got {raw!r}."}) from error


class PipelineRunViewSet(mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet):
    """Runs are a record of what happened; they are never EDITED over the API.

    Deletion is the one exception, and it is deliberate: `corpus.permissions`
    already fixes the policy that generated material is removed by deleting the
    run that produced it, whole, because deleting individual rows would be
    selective reporting. It is also what task 1's 409 on experiment deletion
    tells the operator to do, so the route has to exist.
    """

    serializer_class = PipelineRunSerializer
    # `StaffWrites` allows any authenticated user to read and reserves every
    # unsafe method — DELETE included — for staff.
    permission_classes = [IsAuthenticated, StaffWrites]

    def perform_destroy(self, instance):
        """Refuse while a worker may still hold the run.

        A QUEUED or RUNNING repetition is either waiting for a worker or being
        driven by one right now; deleting its rows mid-flight turns an orderly
        run into a crash inside the task, with half the output already written.
        """
        if instance.status in (
            PipelineRun.Status.QUEUED,
            PipelineRun.Status.RUNNING,
        ):
            raise Conflict(
                f"Repetition {instance.repetition_index} is {instance.status} and "
                "cannot be deleted while a worker may still hold it. Wait for it to "
                "finish, or cancel it first."
            )
        instance.delete()

    def get_queryset(self):
        """Annotate `progress` the same way `ExperimentViewSet` does.

        Without this, `ProgressField` falls back to `PipelineRun.progress`,
        which runs its own aggregate query PER ROW — a page of 50 runs costs
        51 queries instead of 2. `Sum("phase_runs__...")` walks the same
        reverse FK `ProgressField` already knows how to read off an
        annotated instance, so no serializer change is needed.

        The trailing `order_by` has to be RESTATED explicitly — same reason
        `ExperimentViewSet.get_queryset` restates its own: a GROUP BY
        queryset (which this becomes the moment an aggregate annotation is
        present) is not `ordered`, so `Meta.ordering` no longer applies and
        the paginator cannot otherwise rely on page results being stable.

        It is NOT, however, `order_by("experiment_id", "repetition_index")`
        — that used to look like a restatement of
        `PipelineRun.Meta.ordering = ["experiment", "repetition_index"]` but
        was not one: a bare `"experiment"` in `Meta.ordering` is a relation,
        and Django resolves an order-by term that names a relation by
        recursively expanding it through THAT model's own `Meta.ordering`
        (`SQLCompiler.find_ordering_name`) — here, `Experiment.Meta.ordering
        = ["-created_at"]`. So the model's real default order is
        `experiment.created_at DESC, repetition_index ASC`, not `experiment_id
        ASC` — ordering by the raw `experiment_id` column instead silently
        changed the order (verified: two experiments sort `[2, 1]` under the
        real model default, `[1, 2]` under the `experiment_id` one this
        replaces) and left this list inconsistent with `/api/experiments/`,
        which is `-created_at`. Spelled out with an explicit `__created_at`
        lookup below so it does not depend on that relation-expansion
        behavior being understood by whoever reads this next.
        """
        queryset = (
            PipelineRun.objects.select_related("experiment")
            .annotate(
                progress_total=Sum("phase_runs__total_children"),
                progress_completed=Sum("phase_runs__completed_children"),
                progress_failed=Sum("phase_runs__failed_children"),
            )
            .order_by("-experiment__created_at", "repetition_index")
        )
        experiment = integer_param(self.request.query_params, "experiment")
        # `is not None`, not truthiness: `experiment=0` is a real (if
        # nonexistent) id and must filter to nothing, not fall through to
        # "no filter was given" the way a bare `if experiment` would.
        if experiment is not None:
            queryset = queryset.filter(experiment_id=experiment)
        return queryset


class PhaseRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PhaseRunSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def get_queryset(self):
        queryset = PhaseRun.objects.prefetch_related("calls")
        params = self.request.query_params
        if (pipeline_run := integer_param(params, "pipeline_run")) is not None:
            queryset = queryset.filter(pipeline_run_id=pipeline_run)
        if (phase := integer_param(params, "phase")) is not None:
            queryset = queryset.filter(phase=phase)
        if variant := params.get("variant"):
            queryset = queryset.filter(variant=variant)
        if state := params.get("status"):
            queryset = queryset.filter(status=state)
        return queryset

    @action(detail=True, methods=["get"])
    def calls(self, request, pk=None, format=None):
        """Every archived call, in full. Nothing is truncated.

        `format=None`: see `ExperimentViewSet.export` for why every
        `@action` needs it (a `DefaultRouter` format-suffix route, e.g.
        `.../calls.json`, otherwise 500s with `TypeError`).
        """
        queryset = self.get_object().calls.all()
        page = self.paginate_queryset(queryset)
        serializer = LLMCallSerializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None, format=None):
        """Re-label only the scenarios whose call failed.

        `format=None`: see `ExperimentViewSet.export` for why every
        `@action` needs it.
        """
        phase_run = self.get_object()
        if phase_run.phase != 3:
            raise ValidationError(
                {"detail": "Retry re-labels scenarios and applies only to a phase 3 run."}
            )
        if phase_run.pipeline_run_id and not phase_run.source_phase_run_id:
            # `retry_failed` re-derives the scenario list from
            # `source_phase_run` (see `scenarios_for`); with it gone,
            # `scenarios_for` returns nothing to relabel, but `retry_failed`
            # has already DELETED this run's failed calls by that point —
            # an irreversible loss of the only archived record of what
            # happened, in exchange for nothing. Refuse before that runs.
            raise Conflict(
                "The generation run this labelling run derives from no "
                "longer exists, so there is nothing to re-label."
            )
        retry_failed(phase_run)
        # `phase_run` came from `get_queryset()`'s `prefetch_related("calls")`;
        # `retry_failed` deletes and re-creates this run's calls, so that
        # cached `.calls.all()` is now stale. `leak_scan_ran`/`guarded_by`
        # are exactly the isolation evidence this endpoint must not get
        # wrong, so re-fetch a clean instance — no stale prefetch cache —
        # before serializing the response.
        phase_run = PhaseRun.objects.get(pk=phase_run.pk)
        return Response(PhaseRunSerializer(phase_run).data)


class LabelViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LabelSerializer

    def get_queryset(self):
        queryset = Label.objects.select_related("hcv", "scenario")
        params = self.request.query_params
        # `Scenario.scenario_id` (e.g. "sc_01") is unique only WITHIN one
        # system description, not globally — a bare "sc_01" in `?scenario=`
        # would be ambiguous across systems, unlike `hcv` below. This stays
        # primary-key only on purpose.
        for name, field in (
            ("scenario", "scenario_id"),
            ("phase_run", "phase_run_id"),
        ):
            if (value := integer_param(params, name)) is not None:
                queryset = queryset.filter(**{field: value})
        if hcv := params.get("hcv"):
            # A researcher reads `hcv_id: "HCV-07"` off a label's own
            # response and naturally tries `?hcv=HCV-07` next — accept both
            # that and the raw primary key rather than 400ing on the exact
            # value the API just showed them. `filter(hcv_id=...)` below is
            # `Label`'s own FK attname (an integer PK), NOT `HCV.hcv_id` the
            # human-readable CharField — the shared name is a trap, not a
            # coincidence, so don't "simplify" this into `hcv__hcv_id=hcv`
            # for the integer branch. A taxonomy identifier like "HCV-07"
            # never parses as a bare integer, so trying `int()` first is
            # unambiguous, not a guess.
            try:
                queryset = queryset.filter(hcv_id=int(hcv))
            except ValueError:
                queryset = queryset.filter(hcv__hcv_id=hcv)
        if source := params.get("source"):
            queryset = queryset.filter(source=source)
        return queryset
