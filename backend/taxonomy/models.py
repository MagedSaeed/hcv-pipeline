from django.db import models


class Taxonomy(models.Model):
    """A versioned closed label list. Immutable once a run references it."""

    vocabulary_id = models.CharField(max_length=50)
    version = models.CharField(max_length=20)
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)
    source_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "taxonomies"
        constraints = [
            models.UniqueConstraint(
                fields=["vocabulary_id", "version"], name="unique_taxonomy_version"
            )
        ]
        ordering = ["vocabulary_id", "version"]

    def __str__(self):
        return f"{self.vocabulary_id} v{self.version}"

    @classmethod
    def active(cls):
        return cls.objects.filter(is_active=True).order_by("-created_at").first()


class HCV(models.Model):
    """One human-centric vulnerability row of a taxonomy."""

    taxonomy = models.ForeignKey(Taxonomy, related_name="hcvs", on_delete=models.CASCADE)
    hcv_id = models.CharField(max_length=20)
    name = models.CharField(max_length=300)
    definition = models.TextField(blank=True, default="")
    # Empty by design for rows the ontology carries no condition class for.
    condition_classes = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "HCV"
        verbose_name_plural = "HCVs"
        constraints = [
            models.UniqueConstraint(fields=["taxonomy", "hcv_id"], name="unique_hcv_per_taxonomy")
        ]
        ordering = ["order", "hcv_id"]

    def __str__(self):
        return f"{self.hcv_id} {self.name}"

    @property
    def has_condition_class(self):
        return bool(self.condition_classes)
