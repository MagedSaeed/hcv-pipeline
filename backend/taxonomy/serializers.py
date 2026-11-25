from rest_framework import serializers

from taxonomy.categories import validate_mappings
from taxonomy.models import HCV, Taxonomy


def taxonomy_lock_reason(taxonomy):
    """Why this taxonomy's rows can no longer change, or "" if they still can.

    Locked once any run has used it (an iterate run, or a launched repetition of
    an experiment that points at it): runs read the rows live, so editing them
    would change what those runs were shown and guarded against after the fact.
    """
    from analyses.models import AnalysisRun
    from pipeline.models import PhaseRun, PipelineRun

    if (
        PhaseRun.objects.filter(taxonomy=taxonomy).exists()
        or PipelineRun.objects.filter(experiment__taxonomy=taxonomy).exists()
        or AnalysisRun.objects.filter(analysis__taxonomy=taxonomy).exists()
    ):
        return (
            f"{taxonomy} has been used by a run, so its rows are frozen as the record of "
            "what that run saw. Create a new version to make changes."
        )
    return ""


class HCVSerializer(serializers.ModelSerializer):
    class Meta:
        model = HCV
        fields = [
            "id",
            "hcv_id",
            "name",
            "definition",
            "condition_classes",
            "threat_mappings",
            "order",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["threat_mappings"] = data["threat_mappings"] or []
        return data

    def validate_threat_mappings(self, value):
        try:
            return validate_mappings(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error

    def validate_hcv_id(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("An HCV id is required.")
        taxonomy = self.context.get("taxonomy")
        clash = HCV.objects.filter(taxonomy=taxonomy, hcv_id=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if taxonomy is not None and clash.exists():
            raise serializers.ValidationError(f"{value} already exists in this taxonomy.")
        return value

    def validate_name(self, value):
        # Variant A's leak check scans for ids, names and definitions; a row with
        # no name weakens it, so the name is required.
        if not value.strip():
            raise serializers.ValidationError("A name is required.")
        return value.strip()

    def validate_condition_classes(self, value):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise serializers.ValidationError("Must be a list of strings.")
        return [item.strip() for item in value if item.strip()]


class TaxonomySerializer(serializers.ModelSerializer):
    hcv_count = serializers.IntegerField(source="hcvs.count", read_only=True)
    is_locked = serializers.SerializerMethodField()
    locked_reason = serializers.SerializerMethodField()

    class Meta:
        model = Taxonomy
        fields = [
            "id",
            "vocabulary_id",
            "version",
            "notes",
            "is_active",
            "is_analysis_default",
            "is_locked",
            "locked_reason",
            "hcv_count",
            "created_at",
        ]

    def get_locked_reason(self, taxonomy):
        return taxonomy_lock_reason(taxonomy)

    def get_is_locked(self, taxonomy):
        return bool(taxonomy_lock_reason(taxonomy))
