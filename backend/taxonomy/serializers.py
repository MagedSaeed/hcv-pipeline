from rest_framework import serializers

from taxonomy.models import HCV, Taxonomy


class HCVSerializer(serializers.ModelSerializer):
    class Meta:
        model = HCV
        fields = ["id", "hcv_id", "name", "definition", "condition_classes", "order"]


class TaxonomySerializer(serializers.ModelSerializer):
    hcv_count = serializers.IntegerField(source="hcvs.count", read_only=True)

    class Meta:
        model = Taxonomy
        fields = [
            "id",
            "vocabulary_id",
            "version",
            "notes",
            "is_active",
            "hcv_count",
            "created_at",
        ]
