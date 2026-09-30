from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from corpus.models import FewShotExample, Origin, ResourceFile, Scenario, SystemDescription

# The largest real research resource is about 30 KB; 2 MB is generous headroom.
MAX_RESOURCE_BYTES = 2 * 1024 * 1024


class ResourceFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceFile
        fields = [
            "id",
            "name",
            "kind",
            "file",
            "text",
            "description",
            "uploaded_by",
            "created_at",
        ]
        read_only_fields = ["uploaded_by", "created_at"]

    def validate(self, attrs):
        """Read an uploaded file into `text`; the app never re-reads from disk."""
        upload = attrs.get("file")
        if upload is not None:
            # A resource is injected verbatim into LLM prompts, so an oversized
            # one is a context and cost problem long before it is a memory one.
            # Django's DATA_UPLOAD_MAX_MEMORY_SIZE does not bound file uploads.
            if upload.size > MAX_RESOURCE_BYTES:
                raise serializers.ValidationError(
                    {"file": f"File exceeds the {MAX_RESOURCE_BYTES // 1024} KB limit."}
                )
            upload.seek(0)
            try:
                attrs["text"] = upload.read().decode("utf-8")
            except UnicodeDecodeError as error:
                raise serializers.ValidationError(
                    {"file": "File must be UTF-8 text (.txt, .md, .json)."}
                ) from error
            upload.seek(0)
        return attrs


class FewShotExampleSerializer(serializers.ModelSerializer):
    resolved_input = serializers.CharField(read_only=True)
    resolved_output = serializers.CharField(read_only=True)

    class Meta:
        model = FewShotExample
        fields = [
            "id",
            "phase",
            "name",
            "input_text",
            "input_file",
            "output_text",
            "output_file",
            "resolved_input",
            "resolved_output",
            "order",
            "is_active",
        ]

    def validate(self, attrs):
        """Run the model's exactly-one-of rule through the API too."""
        instance = FewShotExample(**{**self._existing(), **attrs})
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs

    def _existing(self):
        """Current values, keyed exactly as incoming attrs are, so a PATCH merges.

        The foreign-key entries must use the field names (`input_file`), not the
        attnames (`input_file_id`). DRF hands validate() the related object under
        the field name, and a dict merge only overrides a key that matches — key
        these by attname and a PATCH validates stale state while the raw payload
        is saved unchecked.
        """
        if self.instance is None:
            return {}
        return {
            field: getattr(self.instance, field)
            for field in (
                "phase",
                "name",
                "input_text",
                "input_file",
                "output_text",
                "output_file",
            )
        }


class SystemDescriptionSerializer(serializers.ModelSerializer):
    is_editable = serializers.BooleanField(read_only=True)
    # See `OriginMixin.is_scratch`. Read off `generation_run`, which the
    # viewset `select_related`s so a page costs no query per row.
    is_scratch = serializers.BooleanField(read_only=True)

    class Meta:
        model = SystemDescription
        fields = [
            "id",
            "title",
            "slug",
            "body",
            "origin",
            "generation_run",
            "is_scratch",
            "is_editable",
            "created_by",
            "created_at",
            "updated_at",
        ]
        # Only the pipeline may write `origin` or `generation_run`; the API
        # always creates `manual` rows, which belong to no run.
        read_only_fields = [
            "slug",
            "origin",
            "generation_run",
            "created_by",
            "created_at",
            "updated_at",
        ]


class ScenarioSerializer(serializers.ModelSerializer):
    is_editable = serializers.BooleanField(read_only=True)
    # See `SystemDescriptionSerializer.is_scratch`.
    is_scratch = serializers.BooleanField(read_only=True)
    card = serializers.DictField(read_only=True)

    class Meta:
        model = Scenario
        fields = [
            "id",
            "system_description",
            "scenario_id",
            "actor",
            "trigger",
            "standing_state",
            "asset",
            "threat",
            "outcome",
            "narrative",
            "intended_hcvs",
            "abox",
            "card",
            "origin",
            "generation_run",
            "is_scratch",
            "is_editable",
            "order",
            "created_by",
            "created_at",
            "updated_at",
        ]
        # Only the pipeline may write `origin` or `generation_run`; the API
        # always creates `manual` rows, which belong to no run.
        read_only_fields = [
            "origin",
            "generation_run",
            "card",
            "created_by",
            "created_at",
            "updated_at",
        ]
        # The model's matching constraint is conditional on generation_run
        # being NULL. generation_run is exposed READ-ONLY — never part of
        # what a request submits — so DRF cannot build this validator from
        # the constraint itself. Every row the API can create has
        # generation_run=NULL (only the pipeline sets it), so this queryset
        # is the API-reachable slice of that constraint.
        validators = [
            UniqueTogetherValidator(
                queryset=Scenario.objects.filter(generation_run__isnull=True),
                fields=("system_description", "scenario_id"),
            )
        ]


DRAFT_CARD_FIELDS = [
    "scenario_id",
    "actor",
    "trigger",
    "standing_state",
    "asset",
    "threat",
    "outcome",
    "narrative",
]


class _DraftScenarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scenario
        fields = DRAFT_CARD_FIELDS


class _DraftSystemSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    body = serializers.CharField(allow_blank=True, required=False, default="")
    scenarios = _DraftScenarioSerializer(many=True)


class _DraftExistingScenarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scenario
        fields = ["system_description", *DRAFT_CARD_FIELDS]
        # Same API-reachable slice of the manual-row constraint as ScenarioSerializer.
        validators = [
            UniqueTogetherValidator(
                queryset=Scenario.objects.filter(generation_run__isnull=True),
                fields=("system_description", "scenario_id"),
            )
        ]

    def validate_system_description(self, system):
        if system.origin == Origin.GENERATED:
            raise serializers.ValidationError(
                "Scenarios can't be added to a generated system: it is a record of model output."
            )
        return system


def _duplicate_ids_errors(rows, key=lambda row: row["scenario_id"]):
    """Errors for repeated scenario IDs within one batch, keyed by row index as
    DRF keys nested list errors, or `None`."""
    seen, errors = set(), {}
    for index, row in enumerate(rows):
        if key(row) in seen:
            errors[str(index)] = {
                "scenario_id": [f"{row['scenario_id']} is used twice in this batch."]
            }
        seen.add(key(row))
    return errors or None


class ScenarioBulkCreateSerializer(serializers.Serializer):
    """New systems with their scenarios, and new scenarios for existing
    systems, created together or not at all."""

    systems = _DraftSystemSerializer(many=True, required=False, default=list)
    scenarios = _DraftExistingScenarioSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        if not attrs["systems"] and not attrs["scenarios"]:
            raise serializers.ValidationError("Add at least one system or scenario.")
        errors = {}
        system_errors = {
            str(index): {"scenarios": duplicates}
            for index, system in enumerate(attrs["systems"])
            if (duplicates := _duplicate_ids_errors(system["scenarios"]))
        }
        if system_errors:
            errors["systems"] = system_errors
        existing = _duplicate_ids_errors(
            attrs["scenarios"],
            key=lambda row: (row["system_description"].pk, row["scenario_id"]),
        )
        if existing:
            errors["scenarios"] = existing
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        created = []
        for draft in validated_data["systems"]:
            system = SystemDescription.objects.create(
                title=draft["title"], body=draft["body"], origin=Origin.MANUAL, created_by=user
            )
            for order, card in enumerate(draft["scenarios"], start=1):
                created.append(
                    Scenario.objects.create(
                        system_description=system,
                        origin=Origin.MANUAL,
                        created_by=user,
                        order=order,
                        **card,
                    )
                )
        for card in validated_data["scenarios"]:
            created.append(Scenario.objects.create(origin=Origin.MANUAL, created_by=user, **card))
        return created
