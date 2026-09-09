import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.keys import MissingAPIKey, resolve_openrouter_key
from core.permissions import StaffWrites as BaseStaffWrites
from pipeline.export import build_bundle, build_run_bundle, safe_filename_stem, write_csv_zip
from pipeline.models import Experiment, Label, PhaseRun, PipelineRun
from pipeline.orchestration import (
    PhaseRunQueueingError,
    QueueingError,
    RunCancelled,
    RunInFlight,
    launch,
    queue_phase_run,
    queue_runs,
    retry_failed,
)
from pipeline.serializers import (
    ExperimentSerializer,
    LabelSerializer,
    LLMCallSerializer,
    PhaseRunCreateSerializer,
    PhaseRunSerializer,
    PipelineRunSerializer,
    RunRequestSerializer,
)

# Surfaced in the `run` action's 202 body (so an operator learns it the
# moment they start a batch) and reused, near-verbatim, by `cancel`'s own
# response — one wording, not two that could drift apart. USER DECISION
# (2026-09-09): cancel is allowed from any non-terminal state, but it does
# NOT reach into a worker already executing; it only marks the run (and its
# still-open phase runs) dead so it becomes deletable.
CANCEL_CAVEAT = (
    "Each of these can be cancelled with POST /api/pipeline-runs/{id}/cancel/ "
    "while it is pending, queued or running. Cancelling marks a run dead so it "
    "can be deleted; it does NOT interrupt a worker that may already be "
    "executing it — any write that worker is mid-way through may still land."
)

# A PipelineRun may be cancelled from any of these — PENDING, QUEUED or
# RUNNING — per the 2026-09-09 user decision quoted above `CANCEL_CAVEAT`.
# Anything else (COMPLETED, FAILED, or already CANCELLED) is a 409.
_CANCELLABLE_RUN_STATUSES = (
    PipelineRun.Status.PENDING,
    PipelineRun.Status.QUEUED,
    PipelineRun.Status.RUNNING,
)


class StaffWrites(BaseStaffWrites):
    """Anyone signed in may read; only staff may create or launch."""

    message = "Only staff may create experiments or launch runs."


class Conflict(APIException):
    """The request is well-formed, but the resource's current state refuses it.

    `default_code` is deliberately generic ("conflict") — every call site
    below passes its OWN `code=`, distinct per rule, the same way
    `MissingAPIKeyError`/`BrokerUnavailableError` give the two 503 cases
    below their own `default_code` rather than sharing one. A frontend can
    then act on `code` instead of pattern-matching the English `detail`.
    """

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


class ExportContentNegotiationMixin:
    """Skip DRF's own content negotiation for an `export` action.

    DRF's `DefaultContentNegotiation` treats `?format=` as its
    `URL_FORMAT_OVERRIDE` too — used to pick a RESPONSE renderer — and
    raises `Http404` the instant that value doesn't name a configured
    renderer's `.format`. `export`'s `?format=csv` (a real, deliberately
    unregistered renderer) and `?format=xml` (its deliberately-invalid case)
    both did that during `initial()`, before `export`'s own body ever ran: a
    404 instead of the 200/400 the action was meant to give. `export`
    always returns a plain `HttpResponse`, never a DRF `Response` —
    `finalize_response` only reads `request.accepted_renderer` for the
    latter — so skipping negotiation here changes nothing else about how it
    behaves. `determine_version` is still called, though: skipping it left
    `request.version`/`request.versioning_scheme` unset, harmless today
    since nothing reads them, but an `AttributeError` waiting for the day a
    versioning class is configured.

    Shared by every viewset with an `export` action (currently
    `ExperimentViewSet` and `PipelineRunViewSet`) rather than duplicating
    this override per viewset.
    """

    def initial(self, request, *args, **kwargs):
        if self.action == "export":
            self.format_kwarg = self.get_format_suffix(**kwargs)
            version, scheme = self.determine_version(request, *args, **kwargs)
            request.version, request.versioning_scheme = version, scheme
            self.perform_authentication(request)
            self.check_permissions(request)
            self.check_throttles(request)
            return
        super().initial(request, *args, **kwargs)


class ConflictCodeMixin:
    """Surface `Conflict`'s `code` in the JSON body, matching the 503 pattern.

    DRF's own exception handler renders a plain `APIException` as
    `{"detail": <message>}` ONLY — the `code` attached to `exc.detail` (an
    `ErrorDetail`, a `str` subclass carrying its own `.code`) never reaches
    the response body on its own; `response.data` from DRF's default
    handler is `{"detail": exc.detail}` with no sibling `code` key at all.
    `_service_unavailable` above already builds the `{"detail", "code"}`
    shape by hand for the two 503 cases, because those are caught and
    rendered manually rather than raised. Every `Conflict` here IS raised —
    through `perform_destroy`, or an `@action` body — and goes through DRF's
    own `handle_exception` -> `settings.EXCEPTION_HANDLER` machinery, so
    that is the one seam common to all of them where `code` can be added
    without hand-building a `Response` at every individual raise site.
    """

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        if isinstance(exc, Conflict) and isinstance(response.data, dict):
            response.data["code"] = exc.detail.code
        return response


class ExperimentViewSet(ConflictCodeMixin, ExportContentNegotiationMixin, viewsets.ModelViewSet):
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

    def perform_destroy(self, instance):
        """Refuse to delete an experiment with any runs recorded against it.

        The spec makes generated output immutable and the archive the record
        of what a model actually produced — cascading a DELETE through an
        experiment's runs, phase runs, calls and labels would erase exactly
        that record, and would do so with a 204 even while a worker still
        holds a RUNNING PipelineRun. An experiment nobody has ever launched
        carries no such record and stays deletable.

        `adhoc_phase_runs` (`PhaseRun.experiment`, `on_delete=PROTECT` — see
        that field's own comment) is checked too, for the identical reason
        `runs` is: an ad-hoc run borrowing this experiment reads its live
        fields at EXECUTION time, so a still-QUEUED/RUNNING one deleted out
        from under its own borrowed experiment would either crash mid-render
        or, if `on_delete` were SET_NULL instead of PROTECT, silently fall
        back to bare defaults — exactly the silent failure this whole field
        exists to prevent. `instance.delete()` would otherwise raise a bare
        `ProtectedError` here (DRF has no handler for it — a 500), so this is
        checked explicitly, with the same actionable 409 shape as `runs`
        rather than left to that crash.
        """
        if instance.runs.exists():
            raise Conflict(
                f"{instance.name!r} has runs recorded against it and cannot be "
                "deleted. Delete each of its runs via DELETE /api/pipeline-runs/"
                "{id}/ first if you really want to remove it.",
                code="experiment_has_runs",
            )
        adhoc_ids = list(instance.adhoc_phase_runs.order_by("id").values_list("id", flat=True))
        if adhoc_ids:
            # Coordinator review round, finding 3: the first version of this
            # message named the route (`DELETE /api/phase-runs/{id}/`) but
            # not which `{id}`s, and `?experiment=` (below) used to be no
            # help either — an operator had a 409 and no way to act on it.
            # Naming the ids here makes the message actually actionable on
            # its own, without a second lookup.
            raise Conflict(
                f"{instance.name!r} has ad-hoc (iterate-mode) phase runs "
                f"{adhoc_ids} borrowing its inputs and cannot be deleted. "
                "Delete each of them via DELETE /api/phase-runs/{id}/ first "
                "if you really want to remove it.",
                code="experiment_has_adhoc_runs",
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

        The 202 body also carries `detail=CANCEL_CAVEAT` — a USER DECISION
        (2026-09-09): an operator starting a batch must learn right then that
        cancelling a run later only marks it dead, it does not reach into a
        worker already executing it, not discover that caveat for the first
        time when they go looking for a way to stop a stuck run.
        """
        experiment = self.get_object()
        options = RunRequestSerializer(data=request.data)
        options.is_valid(raise_exception=True)

        try:
            runs = launch(experiment, n_repetitions=options.validated_data["n_repetitions"])
        except MissingAPIKey as error:
            return _service_unavailable(MissingAPIKeyError, error)
        except DjangoValidationError as error:
            raise ValidationError(getattr(error, "message_dict", {"detail": str(error)})) from error

        try:
            queue_runs(runs)
        except QueueingError as error:
            return _service_unavailable(BrokerUnavailableError, error)

        return Response(
            {
                "runs": [{"id": run.pk, "repetition_index": run.repetition_index} for run in runs],
                "detail": CANCEL_CAVEAT,
            },
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
        matched. See `_export_format` for how the query param and this
        fallback are reconciled, and `_export_response` for how the
        response itself is built — shared with `PipelineRunViewSet.export`.
        """
        experiment = self.get_object()
        wanted = _export_format(request, format)
        bundle = build_bundle(experiment)
        stem = f"{safe_filename_stem(experiment.name)}-export"
        return _export_response(wanted, bundle, stem)


def _service_unavailable(error_class, error):
    """Build a 503 body carrying both the live message and a stable `code`.

    Shared by `ExperimentViewSet.run` and `PhaseRunViewSet.create` — both
    catch a plain, non-DRF exception (`MissingAPIKey`, `QueueingError`,
    `PhaseRunQueueingError`) rather than letting DRF's own exception handler
    render it, since each needs the ORIGINAL exception's dynamic message as
    `detail` — not `error_class`'s generic `default_detail`. Constructing the
    DRF exception anyway (`error_class(str(error))`) and reading its
    `.detail`/`.detail.code` back out means the `code` for each case still
    comes from exactly one place — the exception class's `default_code` —
    rather than a bare string literal repeated wherever a 503 of that kind
    gets built. Module-level, not a method on either viewset: it belongs to
    neither one more than the other, and a method on `ExperimentViewSet`
    would make `PhaseRunViewSet` reach into a sibling class for it.
    """
    exc = error_class(str(error))
    return Response({"detail": str(exc.detail), "code": exc.detail.code}, status=exc.status_code)


def _export_format(request, format):
    """Which format an `export` action was asked for — `json` or `csv`.

    `.lower()`: `?format=CSV` used to 400 for no reason a caller could
    anticipate from the 400's own message. `format` (the URL kwarg a
    format-SUFFIX route like `.../export.csv` passes in — see
    `ExportContentNegotiationMixin`) is used only as a fallback when the
    query param is absent, so `?format=` — what the tests and the frontend
    actually use — still wins. Shared by `ExperimentViewSet.export` and
    `PipelineRunViewSet.export` — the only two `export` actions, and this
    was the first half of what they duplicated.
    """
    wanted = (request.query_params.get("format") or format or "json").lower()
    if wanted not in ("json", "csv"):
        raise ValidationError({"format": "Use format=json or format=csv."})
    return wanted


def _export_response(wanted, bundle, stem):
    """Build the download response for one export bundle, in whichever
    format `_export_format` resolved. The second half of what
    `ExperimentViewSet.export`/`PipelineRunViewSet.export` duplicated — the
    only real difference between the two actions is how `bundle`/`stem` get
    built, not how the response around them is shaped.
    """
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


def integer_param(params, name):
    """Read an integer query parameter, or 400. Never a 500."""
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as error:
        raise ValidationError({name: f"Must be an integer, got {raw!r}."}) from error


class PipelineRunViewSet(
    ConflictCodeMixin,
    ExportContentNegotiationMixin,
    mixins.DestroyModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """Runs are a record of what happened; they are never EDITED over the API.

    Deletion is the one exception, and it is deliberate: `corpus.permissions`
    already fixes the policy that generated material is removed by deleting the
    run that produced it, whole, because deleting individual rows would be
    selective reporting. It is also what task 1's 409 on experiment deletion
    tells the operator to do, so the route has to exist. `cancel` is the other
    exception — the one way out of a run stuck QUEUED or RUNNING forever with
    no worker coming for it (see `cancel`'s own docstring).
    """

    serializer_class = PipelineRunSerializer
    # `StaffWrites` allows any authenticated user to read and reserves every
    # unsafe method — DELETE and `cancel`'s POST included — for staff.
    permission_classes = [IsAuthenticated, StaffWrites]

    def perform_destroy(self, instance):
        """Refuse while a worker may still hold the run.

        A QUEUED or RUNNING repetition is either waiting for a worker or being
        driven by one right now; deleting its rows mid-flight turns an orderly
        run into a crash inside the task, with half the output already written.
        CANCELLED is deliberately not refused here: `cancel` exists precisely
        so a run stuck in one of these two states has a route to become
        deletable, and the 409 below now names that exact route.
        """
        if instance.status in (
            PipelineRun.Status.QUEUED,
            PipelineRun.Status.RUNNING,
        ):
            raise Conflict(
                f"Repetition {instance.repetition_index} is {instance.status} and "
                "cannot be deleted while a worker may still hold it. Wait for it "
                f"to finish, or POST /api/pipeline-runs/{instance.pk}/cancel/ first.",
                code="run_in_flight",
            )
        instance.delete()

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None, format=None):
        """Mark this run — and every non-terminal phase run under it — CANCELLED.

        USER DECISION (2026-09-09): allowed from any non-terminal state —
        PENDING, QUEUED **and** RUNNING, not only the "stuck QUEUED" case
        that motivated it. A terminal run (COMPLETED, FAILED, or already
        CANCELLED) is refused with a 409: there is nothing left to cancel.

        This is the one route out of a run a broker accepted and then died
        on (`max_attempts=1`, `catch_up=False`): before this existed, such a
        run had no way out at all across every registered route — DELETE
        run 409, DELETE experiment 409 (blocked by the run), PATCH/PUT 405,
        DELETE phase-run 405 — and `Experiment.name` is unique, so an
        operator could not even recreate it under the same name.

        `format=None`: see `ExperimentViewSet.export` for why every
        `@action` needs it (a `DefaultRouter` format-suffix route, e.g.
        `.../cancel.json`, otherwise 500s with `TypeError`).

        The response's `detail` is the SAME honest caveat `run`'s 202 already
        carries (`CANCEL_CAVEAT`): this does not reach into a worker that may
        still be executing the run — it only marks the run (and its phase
        runs) dead so they become deletable. A write that worker is mid-way
        through (an `LLMCall` save, a status roll-up) may still land after
        this returns.
        """
        run = self.get_object()
        if run.status not in _CANCELLABLE_RUN_STATUSES:
            raise Conflict(
                f"Repetition {run.repetition_index} is already {run.status} and "
                "cannot be cancelled.",
                code="run_not_cancellable",
            )
        # Two writes (the run itself, then its phase runs) — wrapped so a
        # failure between them cannot leave the run CANCELLED with its phase
        # runs still open, or vice versa.
        with transaction.atomic():
            run.status = PipelineRun.Status.CANCELLED
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at"])
            # Only the phase runs still open — never one already COMPLETED,
            # FAILED, or (e.g. "generation did not complete") already
            # CANCELLED with its own, more specific `error` already
            # recorded, which this must not overwrite. A bulk `.update()`,
            # not a per-row loop with `.save()`: one query regardless of how
            # many phase runs this run has, not an N+1.
            run.phase_runs.exclude(
                status__in=[
                    PhaseRun.Status.COMPLETED,
                    PhaseRun.Status.FAILED,
                    PhaseRun.Status.CANCELLED,
                ]
            ).update(
                status=PhaseRun.Status.CANCELLED,
                finished_at=timezone.now(),
                error="Cancelled by operator.",
            )
        return Response(
            {
                "id": run.pk,
                "status": run.status,
                "detail": CANCEL_CAVEAT,
            }
        )

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None, format=None):
        """Download this one repetition's archival bundle.

        Same `?format=json|csv` contract as `ExperimentViewSet.export` —
        see that action's docstring for why `format=None` is required and
        why content negotiation is skipped for this action (both handled by
        `ExportContentNegotiationMixin`, shared with that viewset). Reuses
        `build_run_bundle`, which itself reuses every per-`phase_run_ids`
        assembly helper `build_bundle` already has — the only thing
        different about a per-run export is its scope and that it carries
        this run's own pinned `config` snapshot as `inputs`, never the
        experiment's current, editable fields (see `PipelineRun.config`).
        """
        run = self.get_object()
        wanted = _export_format(request, format)
        bundle = build_run_bundle(run)
        stem = f"{safe_filename_stem(run.experiment.name)}-rep{run.repetition_index}-export"
        return _export_response(wanted, bundle, stem)

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


class PhaseRunViewSet(
    ConflictCodeMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.ReadOnlyModelViewSet,
):
    """Read-only, plus two deliberate exceptions.

    `create` is iterate mode (spec §5.4/§9, "POST /api/phase-runs/ ad-hoc
    single phase"): run one phase on its own, outside any `Experiment`/
    `PipelineRun`, so a prompt can be tuned without paying for a whole batch.
    `destroy` is how an ad-hoc run's generated material is removed — spec:
    "Generated material is only removed by deleting the run that produced
    it, via DELETE /api/phase-runs/{id}/, which cascades" — the ONLY route
    for an ad-hoc run, which has no `PipelineRun` to delete instead (see
    `PipelineRunViewSet` for that route on an orchestrated run).
    `StaffWrites` (below) reserves BOTH of those — every unsafe method — for
    staff; only `list`/`retrieve` and the two GET `@action`s stay open to any
    authenticated user.
    """

    serializer_class = PhaseRunSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def get_serializer_class(self):
        # `create` writes a narrower, validated shape than `PhaseRunSerializer`
        # exposes for reads (see `PhaseRunCreateSerializer`'s own docstring for
        # why that is a separate class rather than the read serializer reused).
        if self.action == "create":
            return PhaseRunCreateSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        queryset = PhaseRun.objects.prefetch_related("calls")
        params = self.request.query_params
        if (pipeline_run := integer_param(params, "pipeline_run")) is not None:
            queryset = queryset.filter(pipeline_run_id=pipeline_run)
        # `?experiment=` exists on `/api/pipeline-runs/` but used to be
        # silently absent here: `/api/phase-runs/?experiment=1` returned 200
        # with every phase run of every experiment, no matter which one was
        # asked for — the natural isolation query ("all variant-A phase-2
        # runs of experiment X") could not be expressed at all. `pipeline_run
        # __experiment_id` reaches through the FK the same way `?pipeline_run=`
        # already reaches PhaseRun's own FK, one filter added, no query cost
        # for callers that don't use it.
        #
        # `Q(...) | Q(...)`, not `pipeline_run__experiment_id` alone —
        # coordinator review round, finding 3: an ad-hoc run's OWN borrowed
        # `experiment` (`PhaseRun.experiment`, see that field's own comment)
        # never satisfies `pipeline_run__experiment_id` at all (a null
        # `pipeline_run` never matches), so this used to return 0 for the
        # exact ad-hoc runs an operator most needs to find — including the
        # ones blocking `ExperimentViewSet.perform_destroy`'s 409, which
        # named this filter as how to locate them. Matching EITHER FK is
        # what makes "every phase run of experiment X" true regardless of
        # whether a given row got there via `launch()` or via borrowing.
        if (experiment := integer_param(params, "experiment")) is not None:
            queryset = queryset.filter(
                Q(pipeline_run__experiment_id=experiment) | Q(experiment_id=experiment)
            )
        if (phase := integer_param(params, "phase")) is not None:
            queryset = queryset.filter(phase=phase)
        if variant := params.get("variant"):
            queryset = queryset.filter(variant=variant)
        if state := params.get("status"):
            queryset = queryset.filter(status=state)
        return queryset

    def create(self, request, *args, **kwargs):
        """Iterate mode: one phase, ad-hoc, `pipeline_run` left null.

        Mirrors `ExperimentViewSet.run` at every step that also applies here,
        deliberately — the two are the same shape of "validate, then hand off
        to django-q" action, just for a single phase instead of a whole
        batch:

        1. **The API key is resolved FIRST**, exactly as `launch()` does it,
           and for the identical reason (spec §12): a missing key must fail
           before a single row exists, not strand a `PhaseRun` that will
           never be picked up. Done here rather than inside `queue_phase_run`
           because `queue_phase_run` only runs AFTER the row is created —
           too late for "before any row exists".
        2. **Validated the same way the model does.** `PhaseRunCreateSerializer`
           reuses `Experiment.clean()`'s own reasoning (see its docstring) —
           a template pinned to the wrong phase, a taxonomy with no rows, a
           phase-3 run with no `source_phase_run` (or one that is not itself
           a phase-2 run) are all refused before anything is created, the
           same as an orchestrated run would be at `launch()` time.
        3. **Enqueued through `queue_phase_run`** — the ad-hoc counterpart of
           `queue_runs`, built on the SAME broker-failure-revert primitive
           (`pipeline.orchestration._hand_to_broker`) rather than a third,
           hand-written `async_task` call: three independently-maintained
           copies of "mark QUEUED, hand off, revert on failure" is exactly
           the defect shape this codebase has already been bitten by more
           than once (see that module's own docstrings). A broker failure
           here reverts this phase run to PENDING and reports 503 — the row
           still exists (unlike step 1's key check, which runs before any
           row does), so the operator can inspect or re-queue it, matching
           `queue_runs`' own "reverted, not deleted" contract.

        **Isolation is the point of this whole action, not a side detail**:
        an ad-hoc variant-A phase-2 run has no `pipeline_run`, and therefore
        no `PipelineRun.config` snapshot — `pipeline.execution_config
        .resolve_generation_config`'s ad-hoc branch (`_ADHOC_GENERATION_CONFIG`)
        is what it renders against instead. Every isolation layer still
        applies regardless: the context builder still withholds the taxonomy
        from variant A (`pipeline.generation._build_context`), taxonomy-kind
        resources/few-shots are still refused (`prompts.rendering`), the
        render-time backstop still scans the rendered strings
        (`assert_no_taxonomy_leak`), and the final choke point still scans
        the outgoing request immediately before the HTTP call
        (`pipeline.generation._preflight` ->
        `assert_request_not_leaking_taxonomy`) — none of those four guards
        reads `pipeline_run` or the snapshot at all; they are wired onto
        EVERY phase-2 call, ad-hoc or orchestrated alike. See
        `pipeline/tests/test_iterate_mode.py` for this proven end to end.

        Returns 202, not 201: like `run`, this both creates a row AND hands
        it to django-q in the same request — 202 ("accepted, not yet acted
        on") is the honest description of what actually happened, matching
        `run`'s own choice for the identical reason.
        """
        try:
            resolve_openrouter_key()
        except MissingAPIKey as error:
            return _service_unavailable(MissingAPIKeyError, error)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phase_run = serializer.save(created_by=request.user)

        try:
            queue_phase_run(phase_run)
        except PhaseRunQueueingError as error:
            return _service_unavailable(BrokerUnavailableError, error)

        return Response(PhaseRunSerializer(phase_run).data, status=status.HTTP_202_ACCEPTED)

    def perform_destroy(self, instance):
        """Refuse while a worker may still hold this phase run.

        Mirrors `PipelineRunViewSet.perform_destroy` one level down: a QUEUED
        or RUNNING phase run is either waiting for a worker or being driven
        by one right now, and deleting its rows mid-flight turns an orderly
        run into a crash inside the task. This is the ONLY deletion route an
        ad-hoc run has (it has no `PipelineRun` to delete instead), and its
        generated `SystemDescription`/`Scenario` rows cascade with it
        (`generation_run`, `on_delete=CASCADE` — see `corpus.models`), the
        same "delete the run, not individual generated rows" policy
        `corpus.permissions.ReadOnlyIfGenerated` already documents. Works
        the same way for an orchestrated phase run too — nothing here is
        ad-hoc-specific — though the ordinary route for THOSE stays deleting
        the `PipelineRun` itself, which cascades to every phase run under it.
        """
        if instance.status in (PhaseRun.Status.QUEUED, PhaseRun.Status.RUNNING):
            raise Conflict(
                f"{instance} is {instance.status} and cannot be deleted while "
                "a worker may still hold it. Wait for it to finish.",
                code="run_in_flight",
            )
        instance.delete()

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
                "longer exists, so there is nothing to re-label.",
                code="missing_source_phase_run",
            )
        try:
            retry_failed(phase_run)
        except RunCancelled as error:
            # A distinct case from RunInFlight below: a CANCELLED run is
            # terminal and will never finish on its own, so "wait for it to
            # finish" would be actively misleading — its own message and
            # `code` name the real reason instead.
            raise Conflict(str(error), code="retry_run_cancelled") from error
        except RunInFlight as error:
            # `retry_failed` itself refuses a run that is not `is_terminal`
            # (see its docstring) — this is that guard surfacing here as the
            # 409 an API caller can act on, same as every other conflict on
            # this endpoint.
            raise Conflict(str(error), code="retry_run_in_flight") from error
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
    # Explicit so all four viewsets in this module read the same way — this
    # one has no unsafe method for `StaffWrites` to actually gate (read-only,
    # no write action), so the omission was previously harmless in practice,
    # but left a reader unable to tell whether that was deliberate or an
    # oversight, and would have stayed silently wrong the moment a write
    # action was ever added here.
    permission_classes = [IsAuthenticated, StaffWrites]

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
