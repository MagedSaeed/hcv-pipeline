from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify


class ResourceKind(models.TextChoices):
    SYSTEM_DESCRIPTION = "system_description", "System description"
    SCENARIOS = "scenarios", "Scenarios"
    VOCABULARY = "vocabulary", "Vocabulary"
    TAXONOMY = "taxonomy", "Taxonomy"
    OTHER = "other", "Other"


class ResourceFile(models.Model):
    """An uploaded or pasted text resource, referencable from a prompt template."""

    name = models.CharField(max_length=200, unique=True)
    kind = models.CharField(max_length=32, choices=ResourceKind.choices, default=ResourceKind.OTHER)
    file = models.FileField(upload_to="resources/", null=True, blank=True)
    text = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    uploaded_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Origin(models.TextChoices):
    SEED = "seed", "Expert seed"
    GENERATED = "generated", "LLM generated"
    MANUAL = "manual", "Hand authored"


class OriginMixin(models.Model):
    """Shared origin semantics.

    Generated rows are the record of what a model produced. Editing one would
    silently contaminate the measurement, so they are read-only everywhere.
    """

    origin = models.CharField(
        max_length=16, choices=Origin.choices, default=Origin.MANUAL, db_index=True
    )

    class Meta:
        abstract = True

    @property
    def is_editable(self):
        return self.origin != Origin.GENERATED


class SystemDescription(OriginMixin):
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=320, unique=True, blank=True)
    body = models.TextField(blank=True, default="")
    generation_run = models.ForeignKey(
        "pipeline.PhaseRun",
        null=True,
        blank=True,
        related_name="systems",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug(slugify(self.title) or "system")
        super().save(*args, **kwargs)

    def _unique_slug(self, base):
        candidate, suffix = base, 1
        while SystemDescription.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate


class Scenario(OriginMixin):
    CARD_FIELDS = ("actor", "trigger", "standing_state", "asset", "threat", "outcome")

    system_description = models.ForeignKey(
        SystemDescription, related_name="scenarios", on_delete=models.CASCADE
    )
    scenario_id = models.CharField(max_length=50)

    actor = models.TextField(blank=True, default="")
    trigger = models.TextField(blank=True, default="")
    standing_state = models.TextField(blank=True, default="")
    asset = models.TextField(blank=True, default="")
    threat = models.TextField(blank=True, default="")
    outcome = models.TextField(blank=True, default="")

    narrative = models.TextField(blank=True, default="")
    # Phase 1 expert intent. Unused by phases 2 and 3; kept for later scoring.
    intended_hcvs = models.ManyToManyField("taxonomy.HCV", blank=True)
    # Reserved for a future ontology-encoding phase.
    abox = models.JSONField(null=True, blank=True)

    generation_run = models.ForeignKey(
        "pipeline.PhaseRun",
        null=True,
        blank=True,
        related_name="scenarios",
        on_delete=models.CASCADE,
    )

    order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["system_description", "order", "scenario_id"]
        constraints = [
            # Scoped to the run: Phase 2 generates sc_01, sc_02 … against the
            # same target system on every variant and every repetition, so the
            # id is only unique within the run that produced it.
            models.UniqueConstraint(
                fields=["system_description", "generation_run", "scenario_id"],
                name="unique_scenario_id_per_run",
            ),
            # Seed and manual rows all share generation_run=NULL, and NULLs
            # compare as distinct in every database this project supports, so
            # the constraint above does not restrict them at all — without this
            # second, conditional constraint two manually-created rows could
            # collide on the same scenario_id with no error. This is what
            # `seed_corpus`'s update_or_create(generation_run=None, ...) and the
            # `ScenarioSerializer`'s explicit UniqueTogetherValidator both rely
            # on to reject a real duplicate rather than silently doubling it.
            models.UniqueConstraint(
                fields=["system_description", "scenario_id"],
                condition=models.Q(generation_run__isnull=True),
                name="unique_manual_scenario_id_per_system",
            ),
        ]

    def __str__(self):
        return f"{self.system_description.title} / {self.scenario_id}"

    @property
    def card(self):
        return {field: getattr(self, field) for field in self.CARD_FIELDS}


class FewShotExample(models.Model):
    """An input/output demonstration pair injected into a prompt.

    Phase 2 pairs are (system description -> scenarios); phase 3 pairs are
    (scenario -> labels). Each side is inline text or a ResourceFile, never
    both and never neither, so a rendered prompt is never half empty.
    """

    PHASE_CHOICES = [(2, "Phase 2 — generation"), (3, "Phase 3 — labelling")]

    phase = models.PositiveSmallIntegerField(choices=PHASE_CHOICES, db_index=True)
    name = models.CharField(max_length=200)
    input_text = models.TextField(blank=True, default="")
    input_file = models.ForeignKey(
        ResourceFile,
        null=True,
        blank=True,
        related_name="fewshot_inputs",
        on_delete=models.PROTECT,
    )
    output_text = models.TextField(blank=True, default="")
    output_file = models.ForeignKey(
        ResourceFile,
        null=True,
        blank=True,
        related_name="fewshot_outputs",
        on_delete=models.PROTECT,
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["phase", "order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["phase", "name"], name="unique_fewshot_name")
        ]

    def __str__(self):
        return f"[phase {self.phase}] {self.name}"

    def clean(self):
        errors = {}
        for side in ("input", "output"):
            text = bool((getattr(self, f"{side}_text") or "").strip())
            file = getattr(self, f"{side}_file_id") is not None
            if text == file:  # both set, or neither
                errors[f"{side}_text"] = f"Provide exactly one of {side}_text or {side}_file."
        if errors:
            raise ValidationError(errors)

    def _resolve(self, side):
        file = getattr(self, f"{side}_file")
        if file is not None:
            return file.text
        return getattr(self, f"{side}_text")

    @property
    def resolved_input(self):
        return self._resolve("input")

    @property
    def resolved_output(self):
        return self._resolve("output")
