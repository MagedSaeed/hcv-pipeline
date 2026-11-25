"""Saved analyses, kept apart from the research tables; everything cascades from Analysis."""

from django.conf import settings
from django.db import models
from django.db.models import Q

from corpus.models import Scenario

FIRST_MANUAL_ID = 101


class Analysis(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="analyses",
        on_delete=models.SET_NULL,
    )
    name = models.CharField(max_length=300, blank=True, default="")
    description = models.TextField(blank=True, default="")
    # The name the prompts use, frozen with the description so renaming changes no result.
    prompt_title = models.CharField(max_length=300, blank=True, default="")
    taxonomy = models.ForeignKey(
        "taxonomy.Taxonomy", related_name="analyses", on_delete=models.PROTECT
    )
    extraction_settings = models.JSONField(default=dict)
    # Not-assessable areas and no-term statements from the last extraction.
    extraction_gaps = models.JSONField(default=dict, blank=True)
    # The digest of the description the last extraction read; see state.extraction_status.
    extraction_digest = models.CharField(max_length=64, blank=True, default="")
    generation_settings = models.JSONField(default=dict)
    labelling_settings = models.JSONField(default=dict)
    preset_version = models.CharField(max_length=20)
    # The last manual id handed out, so ids are never reused.
    own_sequence = models.PositiveIntegerField(default=FIRST_MANUAL_ID - 1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "analyses"
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return self.name or f"Analysis {self.pk}"


class AnalysisRun(models.Model):
    """One press of Generate or Label, executed as one queued task per model call."""

    class Kind(models.TextChoices):
        EXTRACT = "extract", "Extract"
        GENERATE = "generate", "Generate"
        LABEL = "label", "Label"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    ACTIVE = (Status.QUEUED, Status.RUNNING)

    analysis = models.ForeignKey(Analysis, related_name="runs", on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    # The settings by content: prompts, examples and resources.
    pinned = models.JSONField(default=dict)
    targets = models.JSONField(default=list, blank=True)
    position = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=1)
    done = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    notice = models.TextField(blank=True, default="")
    error = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name="+",
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    progressed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis"],
                condition=Q(status__in=["queued", "running"]),
                name="one_active_run_per_analysis",
            )
        ]

    def __str__(self):
        return f"{self.kind} run {self.pk} ({self.status})"

    @property
    def is_active(self):
        return self.status in self.ACTIVE


class AnalysisItem(models.Model):
    """One extracted value: an element, a fact about one, or a control on one."""

    class Kind(models.TextChoices):
        ELEMENT = "element", "Element"
        FACT = "fact", "Fact"
        CONTROL = "control", "Control"

    class Decision(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    analysis = models.ForeignKey(Analysis, related_name="items", on_delete=models.CASCADE)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    section = models.CharField(max_length=60)
    field = models.CharField(max_length=60)
    # The element's own id, a fact's subject id, or the element a control is on.
    ref = models.CharField(max_length=120, blank=True, default="")
    value = models.CharField(max_length=200, blank=True, default="")
    original_value = models.CharField(max_length=200, blank=True, default="")
    state = models.CharField(max_length=10, blank=True, default="")
    original_state = models.CharField(max_length=10, blank=True, default="")
    fact_type = models.CharField(max_length=10, default="Asserted")
    reason = models.TextField(blank=True, default="")
    other_reading = models.TextField(blank=True, default="")
    problem = models.CharField(max_length=200, blank=True, default="")
    decision = models.CharField(max_length=10, choices=Decision.choices, default=Decision.PENDING)
    order = models.PositiveIntegerField(default=0)
    # Added by the analyst rather than extracted.
    added = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.analysis} / {self.ref} {self.field}"

    @property
    def edited(self):
        changed = self.value != self.original_value or self.state != self.original_state
        return not self.added and changed


class AnalysisScenario(models.Model):
    CARD_FIELDS = Scenario.CARD_FIELDS

    analysis = models.ForeignKey(Analysis, related_name="scenarios", on_delete=models.CASCADE)
    scenario_id = models.CharField(max_length=50)
    actor = models.TextField(blank=True, default="")
    trigger = models.TextField(blank=True, default="")
    standing_state = models.TextField(blank=True, default="")
    asset = models.TextField(blank=True, default="")
    threat = models.TextField(blank=True, default="")
    outcome = models.TextField(blank=True, default="")
    narrative = models.TextField(blank=True, default="")
    is_manual = models.BooleanField(default=False)
    set_aside = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    generated_by = models.ForeignKey(
        AnalysisRun, null=True, blank=True, related_name="scenarios", on_delete=models.CASCADE
    )
    # What the labels were made from; see state.label_key.
    label_key = models.CharField(max_length=64, blank=True, default="")
    labelled_call = models.ForeignKey(
        "AnalysisCall", null=True, blank=True, related_name="+", on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["is_manual", "order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis", "scenario_id"], name="unique_scenario_id_per_analysis"
            )
        ]

    def __str__(self):
        return f"{self.analysis} / {self.scenario_id}"

    @property
    def card(self):
        return {field: getattr(self, field) for field in self.CARD_FIELDS}


class AnalysisCall(models.Model):
    """One archived model call of a run."""

    run = models.ForeignKey(AnalysisRun, related_name="calls", on_delete=models.CASCADE)
    scenario = models.ForeignKey(
        AnalysisScenario, null=True, blank=True, related_name="calls", on_delete=models.SET_NULL
    )
    sequence = models.PositiveIntegerField(default=0)
    rendered_system_prompt = models.TextField(blank=True, default="")
    rendered_user_prompt = models.TextField(blank=True, default="")
    request_json = models.JSONField(null=True, blank=True)
    response_json = models.JSONField(null=True, blank=True)
    raw_response = models.TextField(blank=True, default="")
    model = models.CharField(max_length=200, blank=True, default="")
    finish_reason = models.CharField(max_length=50, blank=True, default="")
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost = models.FloatField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    http_status = models.IntegerField(null=True, blank=True)
    transport_error = models.TextField(blank=True, default="")
    parsed_ok = models.BooleanField(default=False)
    error = models.TextField(blank=True, default="")
    unknown_hcv_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.run} call {self.sequence}"


class AnalysisLabel(models.Model):
    scenario = models.ForeignKey(AnalysisScenario, related_name="labels", on_delete=models.CASCADE)
    hcv = models.ForeignKey("taxonomy.HCV", related_name="+", on_delete=models.PROTECT)
    evidence = models.TextField(blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    call = models.ForeignKey(AnalysisCall, related_name="labels", on_delete=models.CASCADE)

    class Meta:
        ordering = ["hcv__order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["scenario", "hcv"], name="unique_analysis_label")
        ]

    def __str__(self):
        return f"{self.scenario}: {self.hcv.hcv_id}"
