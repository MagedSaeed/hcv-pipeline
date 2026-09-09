from rest_framework import serializers

from prompts.models import PromptTemplate


class PromptTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptTemplate
        fields = [
            "id",
            "phase",
            "name",
            "version",
            "system_prompt",
            "user_prompt",
            "output_schema",
            "is_active",
            "created_by",
            "created_at",
        ]
        # A version is immutable; editing produces a new row, never a mutation.
        # is_active is read-only here too: it is flipped only by the `activate`
        # action, never by a field edit — POST and PATCH must treat it identically
        # (previously POST honoured a supplied is_active while PATCH silently
        # dropped it, which was a bug).
        read_only_fields = ["version", "created_by", "created_at", "is_active"]

    def validate_output_schema(self, value):
        """A JSON Schema is an object. A list or a string round-trips through
        JSONField happily and then reaches the provider as an invalid schema —
        rejected there, on every call of a run, instead of here, once."""
        if not isinstance(value, dict):
            raise serializers.ValidationError(
                f"Must be a JSON object (a JSON Schema), got {type(value).__name__}."
            )
        return value


class PreviewSerializer(serializers.Serializer):
    """The configuration a preview renders against.

    Mirrors what a run would supply, so the preview is not a separate code path.
    """

    variant = serializers.ChoiceField(choices=["A", "B"], required=False)
    taxonomy = serializers.IntegerField(required=False, allow_null=True)
    # Guard-only, and deliberately a SEPARATE field from `taxonomy`: under
    # variant A a taxonomy cannot be supplied at all (the context builder
    # refuses it), so without this the preview always falls back to whatever
    # Taxonomy happens to be active — and with none active it scans nothing,
    # renders, and returns 200. An admin reads that clean preview as proof of
    # isolation. This field is never injected into the context; it only says
    # which taxonomy the backstop scans against, so a preview can also check
    # against the exact version an experiment pins rather than the active one.
    guard_taxonomy = serializers.IntegerField(required=False, allow_null=True)
    seed_systems = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    fewshots = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    generate_systems = serializers.BooleanField(required=False, default=True)
    generate_scenarios = serializers.BooleanField(required=False, default=True)
    n_systems = serializers.IntegerField(required=False, default=3)
    n_scenarios_per_system = serializers.IntegerField(required=False, default=5)
    target_system = serializers.IntegerField(required=False, allow_null=True)
    scenario = serializers.IntegerField(required=False, allow_null=True)
