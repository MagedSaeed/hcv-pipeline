import copy

from django.db import models


class PromptTemplateManager(models.Manager):
    def create_version(self, *, phase, name, **fields):
        """Create the next version of a named template.

        Versions are per (phase, name). Two concurrent calls can race to the
        same number, which the unique constraint turns into an IntegrityError
        rather than a silent overwrite — the loud failure is the point.
        """
        highest = self.filter(phase=phase, name=name).aggregate(models.Max("version"))[
            "version__max"
        ]
        return self.create(phase=phase, name=name, version=(highest or 0) + 1, **fields)


class PromptTemplate(models.Model):
    """A hand-written prompt, versioned so a run can pin exact wording.

    Editing never mutates a row: `new_version()` copies forward. A run stores
    the id it used, so the text behind any stored result stays recoverable.
    """

    PHASE_CHOICES = [(2, "Phase 2 — generation"), (3, "Phase 3 — labelling")]
    COPIED_FIELDS = ("phase", "name", "variant", "system_prompt", "user_prompt", "output_schema")
    VARIANT_CHOICES = [("", "None (phase 3)"), ("A", "Variant A"), ("B", "Variant B")]

    phase = models.PositiveSmallIntegerField(choices=PHASE_CHOICES, db_index=True)
    name = models.CharField(max_length=200)
    version = models.PositiveIntegerField()
    # Which phase 2 variant this template is written for; blank for phase 3.
    variant = models.CharField(max_length=1, choices=VARIANT_CHOICES, blank=True, default="")
    system_prompt = models.TextField(blank=True, default="")
    user_prompt = models.TextField()
    output_schema = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PromptTemplateManager()

    class Meta:
        ordering = ["phase", "name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["phase", "name", "version"], name="unique_prompt_version"
            )
        ]

    def __str__(self):
        return f"[phase {self.phase}] {self.name} v{self.version}"

    @classmethod
    def latest(cls, phase, name):
        return cls.objects.filter(phase=phase, name=name).order_by("-version").first()

    def new_version(self, **changes):
        """Copy this template forward with `changes` applied. Never mutates self.

        Deep-copied because output_schema is a mutable dict: a shallow copy would
        leave both versions' in-memory attributes pointing at one object, so
        editing the new version's schema would silently edit the old one's.
        """
        fields = {field: copy.deepcopy(getattr(self, field)) for field in self.COPIED_FIELDS}
        fields.update(changes)
        phase = fields.pop("phase")
        name = fields.pop("name")
        return PromptTemplate.objects.create_version(phase=phase, name=name, **fields)
