from rest_framework import serializers

from prompts.models import PromptTemplate


def _require_json_object(value):
    """The ONE definition of "a valid output_schema": a JSON Schema is an
    object. A list or a string round-trips through JSONField happily and then
    reaches the provider as an invalid schema — rejected there, on every call
    of a run, instead of here, once.

    Shared between `PromptTemplateSerializer` (a saved version) and
    `PreviewSerializer` (a draft body that may never be saved at all) so the
    two can never drift into checking different things — the same class of
    disagreement `prompts.rendering` warns about repeatedly for the isolation
    guards, now avoided here by construction rather than by discipline.
    """
    if not isinstance(value, dict):
        raise serializers.ValidationError(
            f"Must be a JSON object (a JSON Schema), got {type(value).__name__}."
        )
    return value


class PromptTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptTemplate
        fields = [
            "id",
            "phase",
            "name",
            "version",
            "variant",
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
        return _require_json_object(value)

    def validate(self, attrs):
        phase = attrs.get("phase", getattr(self.instance, "phase", None))
        variant = attrs.get("variant", getattr(self.instance, "variant", ""))
        if phase == 3 and variant:
            raise serializers.ValidationError({"variant": "Phase 3 templates have no variant."})
        return attrs


class PreviewSerializer(serializers.Serializer):
    """The configuration a preview renders against.

    Mirrors what a run would supply, so the preview is not a separate code path.

    `system_prompt`/`user_prompt`/`output_schema` are optional and, when
    present, carry a DRAFT body to render instead of a persisted template's
    stored one — the same three fields `PromptTemplate.COPIED_FIELDS` treats
    as "the text", minus `phase`/`name`. `phase` below is the fourth: needed
    only to preview a template that has no id yet (there is no persisted row
    to read `.phase` off), so it stays optional here and is required by the
    view instead, exactly where the absence of an id makes it load-bearing.
    None of the four is ever sent to a real run — a run always renders a
    persisted, versioned template — so the view is what decides, per request,
    whether they are present and therefore in play; this serializer only
    validates them if so.
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
    n_systems = serializers.IntegerField(required=False, default=1)
    n_scenarios_per_system = serializers.IntegerField(required=False, default=3)
    target_system = serializers.IntegerField(required=False, allow_null=True)
    scenario = serializers.IntegerField(required=False, allow_null=True)

    # --- Draft body: render this instead of a persisted template's stored text ---
    phase = serializers.ChoiceField(choices=PromptTemplate.PHASE_CHOICES, required=False)
    system_prompt = serializers.CharField(required=False, allow_blank=True)
    user_prompt = serializers.CharField(required=False, allow_blank=True)
    output_schema = serializers.JSONField(required=False)

    def validate_output_schema(self, value):
        return _require_json_object(value)
